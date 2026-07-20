import base64
import io
from collections import defaultdict

import matplotlib
# режим Agg работает без окна, картинки сохраняются в память
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .models import Product

# цвета для графиков
BLUE = "#2a78d6"
YELLOW = "#eda100"
RED = "#e34948"

# на сколько процентов уценяем товар
DISCOUNT_PERCENT = 30


def _png(fig):
    # переводим готовый график в base64, чтобы вставить прямо в html
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _next_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


def top_products(limit=10):
    # самые продаваемые товары - сортируем по объёму продажи
    return sorted(Product.objects.all(), key=lambda p: p.sale_volume, reverse=True)[:limit]


def expired_products():
    # товары с истёкшим сроком хранения
    return [p for p in Product.objects.all() if p.is_expired()]


def discount_products():
    # товары которые пролежали больше половины срока и требуют уценки
    return [p for p in Product.objects.all() if p.needs_discount()]


def monthly_revenue():
    # доход магазина по месяцам, считаем по дате поступления товара
    sums = defaultdict(float)
    for p in Product.objects.all():
        sums[(p.arrival_date.year, p.arrival_date.month)] += float(p.revenue())
    return sorted(sums.items())


def linear_fit(months):
    # строим прямую тенденции y = k*x + b методом наименьших квадратов
    # k показывает, на сколько в среднем меняется доход за месяц
    n = len(months)
    if n < 2:
        return None

    xs = list(range(n))
    ys = [value for _, value in months]
    mid_x = sum(xs) / n
    mid_y = sum(ys) / n

    denom = sum((x - mid_x) ** 2 for x in xs)
    if denom == 0:
        return None

    k = sum((xs[i] - mid_x) * (ys[i] - mid_y) for i in range(n)) / denom
    b = mid_y - k * mid_x
    return k, b


def revenue_trend(months):
    # тенденция развития дохода: растёт он или падает и на сколько за месяц
    fit = linear_fit(months)
    if fit is None:
        return None
    k, _ = fit
    return {"slope": abs(k), "growing": k > 0}


def forecast_revenue(months, count=3):
    # прогноз дохода на следующие месяцы по той же прямой тенденции
    fit = linear_fit(months)
    if fit is None:
        return []

    k, b = fit
    n = len(months)
    result = []
    year, month = months[-1][0]
    for i in range(1, count + 1):
        year, month = _next_month(year, month)
        value = k * (n - 1 + i) + b
        # в минус доход уйти не может
        result.append(((year, month), max(value, 0.0)))
    return result


def chart_top_sales():
    # гистограмма: 10 самых продаваемых товаров
    items = top_products(10)
    if not items:
        return None

    names = [p.name for p in items][::-1]
    values = [p.sale_volume for p in items][::-1]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(names, values, color=BLUE)
    ax.set_xlabel("Объём продажи, ед.")
    ax.set_title("10 самых продаваемых товаров")
    # подписываем каждый столбик значением
    for i, v in enumerate(values):
        ax.text(v, i, " " + str(v), va="center", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return _png(fig)


def chart_state_pie():
    # круговая диаграмма: в каком состоянии товары на складе
    products = list(Product.objects.all())
    if not products:
        return None

    expired = sum(1 for p in products if p.is_expired())
    to_discount = sum(1 for p in products if p.needs_discount())
    normal = len(products) - expired - to_discount

    data, labels, colors = [], [], []
    for value, label, color in [
        (normal, "Годные", BLUE),
        (to_discount, "Требуют уценки", YELLOW),
        (expired, "Просроченные", RED),
    ]:
        # нулевые куски на диаграмме не рисуем
        if value > 0:
            data.append(value)
            labels.append(label)
            colors.append(color)

    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    ax.pie(data, labels=labels, colors=colors, autopct="%1.0f%%", startangle=90)
    ax.set_title("Состояние товаров на складе")
    return _png(fig)


def chart_packages():
    # гистограмма: сколько товаров в каждом виде упаковки
    counts = defaultdict(int)
    for p in Product.objects.all():
        counts[p.get_package_display()] += 1
    if not counts:
        return None

    labels = list(counts.keys())
    values = [counts[k] for k in labels]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, values, color=BLUE)
    ax.set_ylabel("Количество товаров")
    ax.set_title("Распределение товаров по видам упаковки")
    for i, v in enumerate(values):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return _png(fig)


def chart_revenue(months, forecast):
    # график: доход по месяцам и прогноз на ближайшие месяцы
    if not months:
        return None

    labels = [f"{m:02d}.{y}" for (y, m), _ in months]
    values = [v for _, v in months]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(range(len(values)), values, color=BLUE, marker="o", linewidth=2, label="Факт")

    # линия тенденции по фактическим месяцам
    fit = linear_fit(months)
    if fit:
        k, b = fit
        trend = [k * x + b for x in range(len(values))]
        ax.plot(range(len(values)), trend, color=RED, linewidth=2, linestyle=":", label="Тенденция")

    if forecast:
        # прогноз рисуем пунктиром, начиная от последней фактической точки
        f_labels = [f"{m:02d}.{y}" for (y, m), _ in forecast]
        f_values = [v for _, v in forecast]
        xs = [len(values) - 1] + list(range(len(values), len(values) + len(f_values)))
        ys = [values[-1]] + f_values
        ax.plot(xs, ys, color=YELLOW, marker="o", linewidth=2, linestyle="--", label="Прогноз")
        labels = labels + f_labels

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("Доход, руб.")
    ax.set_title("Доход магазина по месяцам и прогноз")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return _png(fig)
