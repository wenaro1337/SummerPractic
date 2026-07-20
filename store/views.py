import os
import sqlite3
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect
from django.db.models import Q
from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.db.utils import OperationalError, DatabaseError
from django.utils import timezone
from django.http import HttpResponse

from .models import Product
from .forms import ProductForm
from .errors import error_text
from . import analytics

# какие значения фильтра вообще допустимы
ALLOWED_FILTERS = ("all", "expired", "discount", "top")
MAX_QUERY_LENGTH = 100
# больше 50 МБ базу не принимаем
MAX_DB_SIZE = 50 * 1024 * 1024
# без этих полей загруженная база не сможет работать с текущей моделью товара
REQUIRED_PRODUCT_COLUMNS = {
    "id",
    "code",
    "name",
    "package",
    "arrival_date",
    "shelf_life_days",
    "purchase_volume",
    "sale_volume",
    "price",
    "discounted",
}
PRODUCT_COLUMNS = (
    "code",
    "name",
    "package",
    "arrival_date",
    "shelf_life_days",
    "purchase_volume",
    "sale_volume",
    "price",
    "discounted",
)


def _database_has_product_schema(path):
    # проверяем базу до замены рабочего файла, чтобы не оставить сайт без списка товаров
    con = None
    try:
        con = sqlite3.connect(path)
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='store_product'"
        ).fetchone()
        if row is None:
            return False

        columns = {
            column[1]
            for column in con.execute("PRAGMA table_info(store_product)").fetchall()
        }
        if not REQUIRED_PRODUCT_COLUMNS.issubset(columns):
            return False

        # пробный запрос ловит повреждённую или фактически несовместимую таблицу
        fields = ", ".join(sorted(REQUIRED_PRODUCT_COLUMNS))
        con.execute(f"SELECT {fields} FROM store_product LIMIT 1").fetchone()
        return True
    except (sqlite3.DatabaseError, OSError):
        return False
    finally:
        if con is not None:
            con.close()


def _read_products_database(path):
    # читаем только товары, служебные таблицы загруженного файла не используем
    if not _database_has_product_schema(path):
        return None

    con = None
    try:
        con = sqlite3.connect(path)
        fields = ", ".join(PRODUCT_COLUMNS)
        rows = con.execute(f"SELECT {fields} FROM store_product").fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        if con is not None:
            con.close()

    packages = {value for value, _ in Product.PACKAGE_CHOICES}
    seen_codes = set()
    products = []
    try:
        for row in rows:
            values = dict(zip(PRODUCT_COLUMNS, row))
            code = str(values["code"] or "").strip()
            name = str(values["name"] or "").strip()
            package = str(values["package"] or "")
            arrival = date.fromisoformat(str(values["arrival_date"]))
            shelf_life = int(values["shelf_life_days"])
            purchase = int(values["purchase_volume"])
            sale = int(values["sale_volume"])
            price = Decimal(str(values["price"]))
            code_key = code.casefold()

            if (
                not code
                or len(code) > 20
                or code_key in seen_codes
                or len(name) < 2
                or len(name) > 150
                or package not in packages
                or arrival > timezone.now().date()
                or arrival.year < 2000
                or not 1 <= shelf_life <= 3650
                or not 0 <= purchase <= 1000000
                or not 0 <= sale <= purchase
                or not Decimal("0") < price <= Decimal("1000000")
            ):
                return None

            seen_codes.add(code_key)
            products.append(Product(
                code=code,
                name=name,
                package=package,
                arrival_date=arrival,
                shelf_life_days=shelf_life,
                purchase_volume=purchase,
                sale_volume=sale,
                price=price,
                discounted=bool(values["discounted"]),
            ))
    except (TypeError, ValueError, InvalidOperation):
        return None

    return products


def _write_products_database(path, products):
    # отдельная база содержит только таблицу товара и не затрагивает админку Django
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        con.execute("""
            CREATE TABLE store_product (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(20) NOT NULL UNIQUE,
                name VARCHAR(150) NOT NULL,
                package VARCHAR(20) NOT NULL,
                arrival_date DATE NOT NULL,
                shelf_life_days INTEGER NOT NULL,
                purchase_volume INTEGER NOT NULL,
                sale_volume INTEGER NOT NULL,
                price DECIMAL NOT NULL,
                discounted BOOLEAN NOT NULL DEFAULT 0
            )
        """)
        con.executemany(
            """
            INSERT INTO store_product (
                code, name, package, arrival_date, shelf_life_days,
                purchase_volume, sale_volume, price, discounted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    product.code,
                    product.name,
                    product.package,
                    product.arrival_date.isoformat(),
                    product.shelf_life_days,
                    product.purchase_volume,
                    product.sale_volume,
                    str(product.price),
                    int(product.discounted),
                )
                for product in products
            ],
        )
        con.commit()
    finally:
        con.close()


def _find_product(pk):
    # ищем товар по id, если его нет - вернём None
    try:
        return Product.objects.get(pk=pk)
    except (Product.DoesNotExist, ValueError):
        return None


def product_list(request):
    # главная страница со списком товаров
    # если задан параметр поиска q - отбираем товары по коду или названию
    query = request.GET.get("q", "").strip()
    current_filter = request.GET.get("filter", "all")

    # проверяем что фильтр выбран из списка, а не подставлен вручную в адрес
    if current_filter not in ALLOWED_FILTERS:
        messages.error(request, error_text("E301"))
        current_filter = "all"

    # слишком длинный запрос обрезаем и предупреждаем
    if len(query) > MAX_QUERY_LENGTH:
        messages.error(request, error_text("E302"))
        query = query[:MAX_QUERY_LENGTH]

    db_ok = True
    try:
        products = Product.objects.all()
        if query:
            products = products.filter(
                Q(code__icontains=query) | Q(name__icontains=query)
            )
        # если в загруженной базе нет таблицы товаров будет предупреждение
        products = list(products)
    except (OperationalError, DatabaseError):
        products = []
        db_ok = False

    # фильтры по заданию
    if current_filter == "expired":
        products = [p for p in products if p.is_expired()]
    elif current_filter == "discount":
        products = [p for p in products if p.needs_discount()]
    elif current_filter == "top":
        # самые продаваемые - берём первую десятку по объёму продажи
        products = sorted(products, key=lambda p: p.sale_volume, reverse=True)[:10]

    return render(request, "store/product_list.html", {
        "products": products,
        "query": query,
        "db_ok": db_ok,
        "db_error": error_text("E304"),
        "current_filter": current_filter,
    })


def product_create(request):
    # добавление нового товара
    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Товар добавлен.")
            return redirect("product_list")
    else:
        form = ProductForm()

    return render(request, "store/product_form.html", {
        "form": form,
        "title": "Добавление товара",
    })


def product_update(request, pk):
    # редактирование выбранного товара
    product = _find_product(pk)
    if product is None:
        messages.error(request, error_text("E303"))
        return redirect("product_list")

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "Изменения сохранены.")
            return redirect("product_list")
    else:
        form = ProductForm(instance=product)

    return render(request, "store/product_form.html", {
        "form": form,
        "title": "Редактирование товара",
    })


def product_delete(request, pk):
    # удаление товара + страница подтверждения
    product = _find_product(pk)
    if product is None:
        messages.error(request, error_text("E303"))
        return redirect("product_list")

    if request.method == "POST":
        product.delete()
        messages.success(request, "Товар удалён.")
        return redirect("product_list")

    return render(request, "store/product_confirm_delete.html", {
        "product": product,
    })


def database_upload(request):
    # загружаем из файла только товары, саму рабочую базу Django не заменяем
    if request.method != "POST":
        return redirect("product_list")

    upload = request.FILES.get("dbfile")
    if not upload:
        messages.error(request, error_text("E201"))
        return redirect("product_list")

    # слишком большой файл дальше не пускаем
    if upload.size > MAX_DB_SIZE:
        messages.error(request, error_text("E202"))
        return redirect("product_list")

    # сохраняем загруженный файл во временный
    backups_dir = os.path.join(settings.BASE_DIR, "backups")
    os.makedirs(backups_dir, exist_ok=True)
    tmp_path = os.path.join(backups_dir, "_uploaded_tmp.sqlite3")
    with open(tmp_path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    products = _read_products_database(tmp_path)
    if products is None:
        os.remove(tmp_path)
        messages.error(request, error_text("E203"))
        return redirect("product_list")

    # сохраняем отдельную резервную копию текущих товаров
    stamp = timezone.now().strftime("%d-%m-%Y_%H-%M-%S")
    backup_name = f"products_backup_{stamp}.sqlite3"
    backup_path = os.path.join(backups_dir, backup_name)
    try:
        _write_products_database(backup_path, Product.objects.all())
        with transaction.atomic():
            Product.objects.all().delete()
            Product.objects.bulk_create(products)
    except (OSError, sqlite3.DatabaseError, DatabaseError):
        if os.path.exists(backup_path):
            os.remove(backup_path)
        messages.error(request, error_text("E204"))
        return redirect("product_list")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    messages.success(
        request,
        f"Товары успешно загружены. Резервная копия прежних товаров: backups/{backup_name}",
    )
    return redirect("product_list")


def database_export(request):
    # выгружаем только товары без пользователей, прав и других таблиц Django
    temp_path = None
    try:
        handle, temp_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.remove(temp_path)
        _write_products_database(temp_path, Product.objects.all())
        with open(temp_path, "rb") as source:
            database_bytes = source.read()
    except (OSError, sqlite3.DatabaseError, DatabaseError):
        messages.error(request, error_text("E205"))
        return redirect("product_list")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    stamp = timezone.now().strftime("%d-%m-%Y_%H-%M-%S")
    response = HttpResponse(database_bytes, content_type="application/vnd.sqlite3")
    response["Content-Disposition"] = (
        f'attachment; filename="magazin_products_{stamp}.sqlite3"'
    )
    return response


def analytics_page(request):
    # страница аналитики: сводка по заданию и графики
    try:
        has_products = Product.objects.exists()
    except (OperationalError, DatabaseError):
        has_products = False

    if not has_products:
        return render(request, "store/analytics.html", {"empty": True})

    months = analytics.monthly_revenue()
    forecast = analytics.forecast_revenue(months, 3)
    top = analytics.top_products(10)

    return render(request, "store/analytics.html", {
        "empty": False,
        # самый продаваемый товар - первый в списке лидеров
        "best": top[0],
        "expired": analytics.expired_products(),
        "to_discount": analytics.discount_products(),
        "discount_percent": analytics.DISCOUNT_PERCENT,
        "trend": analytics.revenue_trend(months),
        "month_rows": [{"label": f"{m:02d}.{y}", "value": v} for (y, m), v in months],
        "forecast_rows": [{"label": f"{m:02d}.{y}", "value": v} for (y, m), v in forecast],
        "total_revenue": sum(v for _, v in months),
        "chart_sales": analytics.chart_top_sales(),
        "chart_state": analytics.chart_state_pie(),
        "chart_packages": analytics.chart_packages(),
        "chart_revenue": analytics.chart_revenue(months, forecast),
    })


def apply_discount(request):
    # уценка товаров, которые пролежали больше половины срока хранения
    if request.method != "POST":
        return redirect("analytics")

    rate = Decimal(100 - analytics.DISCOUNT_PERCENT) / Decimal(100)
    count = 0
    for product in Product.objects.all():
        if product.needs_discount():
            product.price = (product.price * rate).quantize(Decimal("0.01"))
            product.discounted = True
            product.save()
            count += 1

    if count:
        messages.success(request, f"Уценено товаров: {count} (скидка {analytics.DISCOUNT_PERCENT}%)")
    else:
        messages.success(request, "Товаров для уценки не нашлось")
    return redirect("analytics")
