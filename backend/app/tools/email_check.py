"""Email deliverability check and test send, for go-live.

  python -m app.tools.email_check nearbnb.com --selector mail      # DNS: SPF, DKIM, DMARC
  python -m app.tools.email_check nearbnb.com --send me@gmail.com  # sends a real test email with the live SMTP settings

Prints PASS/WARN/FAIL per check and exits 1 if anything failed. Run it from the api container on the server.
"""
import argparse
import sys
from types import SimpleNamespace

import dns.exception
import dns.resolver


def txt_records(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=8)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    return ["".join(part.decode() for part in rdata.strings) for rdata in answers]


def check_spf(records: list[str]) -> tuple[str, str]:
    spf = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf:
        return "FAIL", "no SPF record (a TXT record starting v=spf1). Without it many inboxes send your mail to spam."
    if len(spf) > 1:
        return "FAIL", "more than one SPF record: receivers reject this. Merge them into one."
    record = spf[0].lower()
    if record.endswith("+all") or record.endswith(" all") and not record.endswith(("-all", "~all", "?all")):
        return "FAIL", "SPF ends in +all, which lets anyone send as you."
    if "-all" not in record and "~all" not in record:
        return "WARN", f"SPF has no -all or ~all ending: {spf[0]}"
    return "PASS", spf[0]


def check_dmarc(records: list[str]) -> tuple[str, str]:
    dmarc = [r for r in records if r.lower().replace(" ", "").startswith("v=dmarc1")]
    if not dmarc:
        return "FAIL", "no DMARC record at _dmarc.<domain>. Start with: v=DMARC1; p=none; rua=mailto:you@your-domain"
    policy = next((p.split("=", 1)[1].strip().lower() for p in dmarc[0].replace(" ", "").split(";") if p.startswith("p=")), "")
    if policy == "none":
        return "WARN", "DMARC is in monitor mode (p=none). Fine to launch; move to p=quarantine once reports look clean."
    return "PASS", dmarc[0]


def check_dkim(records: list[str]) -> tuple[str, str]:
    key = [r for r in records if "v=dkim1" in r.lower().replace(" ", "") or "p=" in r.lower()]
    if not key:
        return "FAIL", "no DKIM key at <selector>._domainkey.<domain>. Your mail provider shows the selector and the record to add."
    return "PASS", "DKIM key found"


def run_dns(domain: str, selector: str | None) -> list[tuple[str, str, str]]:
    results = [("SPF", *check_spf(txt_records(domain))), ("DMARC", *check_dmarc(txt_records(f"_dmarc.{domain}")))]
    if selector:
        results.append(("DKIM", *check_dkim(txt_records(f"{selector}._domainkey.{domain}"))))
    else:
        results.append(("DKIM", "WARN", "skipped: pass --selector <name> (shown by your mail provider)"))
    return results


def send_test(to: str) -> tuple[str, str]:
    from app.core.config import get_settings
    from app.modules.notifications.service import _send_smtp

    if not get_settings().smtp_host:
        return "FAIL", "SMTP_HOST is empty, so nothing would really be sent."
    row = SimpleNamespace(to_email=to, subject="NearBnB test email", body_text="If you can read this, outgoing email works.\nCheck it landed in the inbox, not spam.", body_html=None)
    try:
        _send_smtp(row)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"send failed: {exc}"
    return "PASS", f"sent to {to}. Now check the inbox AND the spam folder."


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("domain")
    p.add_argument("--selector")
    p.add_argument("--send", metavar="EMAIL")
    args = p.parse_args(argv)
    results = run_dns(args.domain, args.selector)
    if args.send:
        results.append(("SEND", *send_test(args.send)))
    for name, status, detail in results:
        print(f"{status:5} {name:6} {detail}")
    return 1 if any(s == "FAIL" for _, s, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
