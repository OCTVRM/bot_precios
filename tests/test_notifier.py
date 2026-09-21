from src.services.notifier import AlertPayload, TelegramNotifier


def test_telegram_message_format():
    """Verifica que el mensaje en HTML contenga todos los campos estructurados requeridos."""
    notifier = TelegramNotifier()
    payload = AlertPayload(
        title="Monitor Gamer 27\" <4K UHD>",  # Caracteres especiales para probar escaping
        store="Falabella",
        old_price=500000.0,
        new_price=350000.0,
        discount_percent=30.0,
        affiliate_url="https://falabella.com/p/123",
        is_all_time_low=True,
        target_price=400000.0,
    )

    msg = notifier.format_message(payload)

    # Verificaciones visuales y de formato HTML con moneda oficial CLP
    assert "OFERTA DETECTADA EN FALABELLA" in msg
    assert "&lt;4K UHD&gt;" in msg        # HTML escapado
    assert "<s>$500.000</s>" in msg       # Precio anterior tachado en CLP
    assert "<b>$350.000</b>" in msg       # Precio nuevo destacado en CLP
    assert "<b>$150.000</b>" in msg or "$150.000" in msg  # Ahorro en CLP
    assert "-30.0%" in msg                # Porcentaje de ahorro
    assert "MÍNIMO HISTÓRICO REGISTRADO" in msg
    assert "Alcanzó tu precio objetivo de $400.000" in msg


def test_telegram_inline_keyboard():
    """Verifica la generación del botón interactivo de afiliado."""
    notifier = TelegramNotifier()
    payload = AlertPayload(
        title="Laptop Pro",
        store="Mercado Libre",
        old_price=1000.0,
        new_price=800.0,
        discount_percent=20.0,
        affiliate_url="https://mercadolibre.com/p/123?matt_tool=tag",
    )

    keyboard = notifier.build_keyboard(payload)
    button = keyboard.inline_keyboard[0][0]

    assert "Mercado Libre" in button.text
    assert button.url == "https://mercadolibre.com/p/123?matt_tool=tag"


def test_telegram_message_price_error_and_category():
    """Verifica que un descuento de >=50% o error de precio active la cabecera de bug y muestre la categoría."""
    notifier = TelegramNotifier()
    payload = AlertPayload(
        title="Smart TV OLED 65 pulgadas",
        store="Ripley",
        old_price=1000000.0,
        new_price=300000.0,
        discount_percent=70.0,
        affiliate_url="https://simple.ripley.cl/tv-oled",
        is_all_time_low=True,
        category="Televisores y Smart TV",
        is_price_error=True,
    )

    msg = notifier.format_message(payload)

    assert "POSIBLE ERROR DE PRECIO / BUG EN RIPLEY" in msg
    assert "Descuento anómalo del 70.0%" in msg
    assert "Categoría:</b> Televisores y Smart TV" in msg
    assert "<s>$1.000.000</s>" in msg
    assert "<b>$300.000</b>" in msg

