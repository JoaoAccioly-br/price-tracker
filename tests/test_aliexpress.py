from app.scrapers import aliexpress


def test_aliexpress_extract_from_url():
    url = (
        "https://pt.aliexpress.com/item/1005009929965236.html?"
        "pdp_npi=6%40dis%21BRL%21229.01%2199.93%21%21%21283.25%21123.59"
    )
    orig, promo = aliexpress.extract_from_url(url)
    assert orig == 229.01
    assert promo == 99.93


def test_aliexpress_parse_with_url():
    html_sample = """
    <html>
      <head>
        <meta property="og:title" content="Mouse Attack Shark X11 - AliExpress" />
      </head>
      <body></body>
    </html>
    """
    url = "https://pt.aliexpress.com/item/123.html?pdp_npi=6@dis!BRL!200.00!150.00"
    res = aliexpress.parse(html_sample, url=url)
    assert res.price == 200.00
    assert res.final_price == 150.00
    assert res.name == "Mouse Attack Shark X11"
    assert res.available is True
    assert res.source == "aliexpress:url-npi"


def test_aliexpress_parse_without_pdp_npi():
    html_sample = "<html><head></head><body></body></html>"
    res = aliexpress.parse(html_sample, url="https://pt.aliexpress.com/item/123.html")
    assert res.price is None
    assert res.available is False
    assert res.source == "aliexpress:csr-js-required"

