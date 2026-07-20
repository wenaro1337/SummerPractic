import re
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Product
from .errors import error_text

# что можно вводить в коде продукта
CODE_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЁё0-9\-_]+$")
MAX_VOLUME = 1000000
MAX_PRICE = Decimal("1000000")
MAX_SHELF_LIFE = 3650


class ProductForm(forms.ModelForm):
    # форма для добавления и правки товара
    class Meta:
        model = Product
        fields = [
            "code",
            "name",
            "package",
            "arrival_date",
            "shelf_life_days",
            "purchase_volume",
            "sale_volume",
            "price",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "field"}),
            "name": forms.TextInput(attrs={"class": "field"}),
            "package": forms.Select(attrs={"class": "field"}),
            # format гггг-мм-дд нужен, чтобы поле type="date" подставляло
            # сохранённую дату при редактировании товара.
            "arrival_date": forms.DateInput(attrs={"class": "field", "type": "date"}, format="%Y-%m-%d"),
            "shelf_life_days": forms.NumberInput(attrs={"class": "field"}),
            "purchase_volume": forms.NumberInput(attrs={"class": "field"}),
            "sale_volume": forms.NumberInput(attrs={"class": "field"}),
            "price": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
        }
        # стандартные ошибки Django заменяем на свои с кодами
        error_messages = {
            "code": {
                "required": error_text("E101"),
                "max_length": error_text("E102"),
                "unique": error_text("E104"),
            },
            "name": {
                "required": error_text("E105"),
                "max_length": error_text("E106"),
            },
            "package": {
                "required": error_text("E107"),
                "invalid_choice": error_text("E107"),
            },
            "arrival_date": {
                "required": error_text("E108"),
                "invalid": error_text("E108"),
            },
            "shelf_life_days": {
                "required": error_text("E111"),
                "invalid": error_text("E111"),
                "min_value": error_text("E112"),
            },
            "purchase_volume": {
                "required": error_text("E114"),
                "invalid": error_text("E114"),
                "min_value": error_text("E114"),
            },
            "sale_volume": {
                "required": error_text("E116"),
                "invalid": error_text("E116"),
                "min_value": error_text("E116"),
            },
            "price": {
                "required": error_text("E118"),
                "invalid": error_text("E118"),
            },
        }

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip()
        if not code:
            raise forms.ValidationError(error_text("E101"))
        if not CODE_PATTERN.match(code):
            raise forms.ValidationError(error_text("E103"))
        # проверяем что такого кода ещё нет (сам себя при правке не считаем)
        same = Product.objects.filter(code__iexact=code)
        if self.instance.pk:
            same = same.exclude(pk=self.instance.pk)
        if same.exists():
            raise forms.ValidationError(error_text("E104"))
        return code

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError(error_text("E105"))
        if len(name) < 2:
            raise forms.ValidationError(error_text("E106"))
        return name

    def clean_arrival_date(self):
        value = self.cleaned_data.get("arrival_date")
        if not value:
            raise forms.ValidationError(error_text("E108"))
        if value > timezone.now().date():
            raise forms.ValidationError(error_text("E109"))
        if value.year < 2000:
            raise forms.ValidationError(error_text("E110"))
        return value

    def clean_shelf_life_days(self):
        value = self.cleaned_data.get("shelf_life_days")
        if value is None:
            raise forms.ValidationError(error_text("E111"))
        if value < 1:
            raise forms.ValidationError(error_text("E112"))
        if value > MAX_SHELF_LIFE:
            raise forms.ValidationError(error_text("E113"))
        return value

    def clean_purchase_volume(self):
        value = self.cleaned_data.get("purchase_volume")
        if value is None:
            raise forms.ValidationError(error_text("E114"))
        if value > MAX_VOLUME:
            raise forms.ValidationError(error_text("E115"))
        return value

    def clean_sale_volume(self):
        value = self.cleaned_data.get("sale_volume")
        if value is None:
            raise forms.ValidationError(error_text("E116"))
        if value > MAX_VOLUME:
            raise forms.ValidationError(error_text("E116"))
        return value

    def clean_price(self):
        value = self.cleaned_data.get("price")
        if value is None:
            raise forms.ValidationError(error_text("E118"))
        if value <= 0:
            raise forms.ValidationError(error_text("E119"))
        if value > MAX_PRICE:
            raise forms.ValidationError(error_text("E120"))
        return value

    def clean(self):
        # проверяем, что нельзя продать больше, чем закупили
        cleaned = super().clean()
        buy = cleaned.get("purchase_volume")
        sell = cleaned.get("sale_volume")
        if buy is not None and sell is not None and sell > buy:
            self.add_error("sale_volume", error_text("E117"))
        return cleaned
