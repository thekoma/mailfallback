def test_apprise_importable():
    import apprise

    a = apprise.Apprise()
    assert a is not None
