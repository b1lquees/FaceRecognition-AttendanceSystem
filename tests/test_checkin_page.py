# client and login come from conftest.py


def test_the_check_in_page_needs_a_login(client):
    assert client.get("/checkin").status_code == 302  # sent to the login page


def test_any_signed_in_user_can_open_it(client, login):
    login()  # an ordinary viewer, like a student

    page = client.get("/checkin").get_data(as_text=True)

    assert "Start check-in" in page
    assert "vendor/jsQR.js" in page  # the QR reader is loaded from our own server


def test_the_qr_reader_is_served_from_our_own_server(client, login):
    login()
    response = client.get("/static/vendor/jsQR.js")

    assert response.status_code == 200
    assert b"jsQR" in response.data
    response.close()


def test_the_qr_reader_script_carries_this_pages_nonce(client, login):
    # the security policy only runs scripts tagged with this response's nonce, so an
    # untagged <script src> would silently never load
    login()
    response = client.get("/checkin")
    nonce = response.headers["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]

    assert f'nonce="{nonce}" src="/static/vendor/jsQR.js"' in response.get_data(as_text=True)
