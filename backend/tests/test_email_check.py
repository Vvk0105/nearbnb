from app.tools.email_check import check_dkim, check_dmarc, check_spf


def test_spf_verdicts():
    assert check_spf([])[0] == "FAIL"
    assert check_spf(["v=spf1 include:_spf.example.com -all"])[0] == "PASS"
    assert check_spf(["v=spf1 include:_spf.example.com ~all"])[0] == "PASS"
    assert check_spf(["v=spf1 include:a -all", "v=spf1 include:b -all"])[0] == "FAIL"
    assert check_spf(["v=spf1 +all"])[0] == "FAIL"
    assert check_spf(["v=spf1 include:a"])[0] == "WARN"
    assert check_spf(["google-site-verification=abc"])[0] == "FAIL"


def test_dmarc_verdicts():
    assert check_dmarc([])[0] == "FAIL"
    assert check_dmarc(["v=DMARC1; p=none; rua=mailto:a@b.c"])[0] == "WARN"
    assert check_dmarc(["v=DMARC1; p=quarantine"])[0] == "PASS"
    assert check_dmarc(["v=DMARC1;p=reject"])[0] == "PASS"


def test_dkim_verdicts():
    assert check_dkim([])[0] == "FAIL"
    assert check_dkim(["v=DKIM1; k=rsa; p=MIGfMA0"])[0] == "PASS"
