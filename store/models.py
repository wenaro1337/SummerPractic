from django.db import models
from django.utils import timezone
from datetime import timedelta


class Product(models.Model):
    # одна запись = один продукт на складе

    # различные виды упаковки можно добавить ещё
    PACKAGE_CHOICES = [
        ("bottle", "Бутылка"),
        ("pack", "Пакет"),
        ("box", "Коробка"),
        ("can", "Банка"),
        ("bag", "Мешок"),
        ("weight", "Развес (без упаковки)"),
    ]

    code = models.CharField("Код продукта", max_length=20, unique=True)
    name = models.CharField("Название", max_length=150)
    package = models.CharField("Вид упаковки", max_length=20, choices=PACKAGE_CHOICES, default="pack")
    arrival_date = models.DateField("Дата поступления", default=timezone.now)
    # срок хранения храниться в днях - так удобнее считать просрочку и уценку
    shelf_life_days = models.PositiveIntegerField("Срок хранения, дней", default=30)
    purchase_volume = models.PositiveIntegerField("Объём закупки", default=0)
    sale_volume = models.PositiveIntegerField("Объём продажи", default=0)
    price = models.DecimalField("Цена, руб.", max_digits=10, decimal_places=2, default=0)
    # отметка что товар уже уценили, чтобы не уценить его второй раз
    discounted = models.BooleanField("Уценён", default=False)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def expiry_date(self):
        # дата до которой товар годен
        return self.arrival_date + timedelta(days=self.shelf_life_days)

    def is_expired(self):
        # истёк ли срок хранения на сегодня
        return self.expiry_date() < timezone.now().date()

    def remainder(self):
        # остаток на складе: закупили минус продали
        return self.purchase_volume - self.sale_volume

    def shelf_life_used(self):
        # сколько процентов срока хранения уже прошло
        if self.shelf_life_days == 0:
            return 100
        days_passed = (timezone.now().date() - self.arrival_date).days
        return round(days_passed / self.shelf_life_days * 100)

    def needs_discount(self):
        # кандидат на уценку: пролежал больше половины срока,
        # но ещё не просрочен и не уценён
        return (
            not self.is_expired()
            and not self.discounted
            and self.shelf_life_used() > 50
        )

    def revenue(self):
        # доход с товара: сколько продали умножить на цену
        return self.sale_volume * self.price
