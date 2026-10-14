import base64
import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from empleo.gmail import Gmail, body_hash
from empleo.runner import blocked, prepare, reconcile, run, rank
from empleo.search import canonical, discover, extract
from empleo.storage import Conflict, GitHubStore, LocalStore
from empleo.__main__ import main


def history():
    return {k: [] for k in ("offers", "aliases", "runs", "observations", "deliveries", "events")}


def job(**changes):
    data = {"@type": "JobPosting", "title": "Psicólogo General Sanitario",
            "hiringOrganization": {"name": "Centro de ejemplo"},
            "jobLocation": {"address": {"addressLocality": "Madrid"}},
            "description": "Atención a adultos bajo contrato laboral",
            "datePosted": "2026-10-14", "validThrough": "2026-12-31",
            "url": "https://example.org/empleo/1"}
    data.update(changes)
    return '<script type="application/ld+json">' + json.dumps(data) + "</script>"


def offer():
    return extract(job(), "https://example.org/empleo/1", date(2026, 10, 14))[0]


class MemoryStore:
    def __init__(self, state=None, fail_at=None):
        self.data = copy.deepcopy(state or history())
        self.calls = 0
        self.fail_at = fail_at

    def save(self, data):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("Persistencia no disponible")
        self.data = copy.deepcopy(data)


class Mail:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def send(self, *args):
        self.calls += 1
        if self.fail:
            raise TimeoutError()
        return "gmail-confirmed-id"

    def reconcile(self, delivery):
        return None


class SearchTests(unittest.TestCase):
    def test_future_publication_rejected(self):
        self.assertEqual(extract(job(datePosted="2026-11-01"), "https://example.org",
                                 date(2026, 10, 14)), [])

    def test_parse_actual_job_fields(self):
        item = offer()
        self.assertEqual(item["employer"], "Centro de ejemplo")
        self.assertEqual(item["published_at"], "2026-10-14")
        self.assertEqual(item["priority"], 1)
        self.assertIsNone(item["active"])

    def test_expired_forensic_outside_madrid_rejected(self):
        for changes in ({"validThrough": "2026-10-13"}, {"title": "Psicólogo forense"},
                        {"jobLocation": {"address": {"addressLocality": "Barcelona"}}}):
            self.assertEqual(extract(job(**changes), "https://example.org", date(2026, 10, 14)), [])

    def test_old_initial_postings_and_invalid_markup(self):
        self.assertEqual(extract(job(datePosted="2025-01-01"), "https://example.org",
                                 date(2026, 10, 14), initial=True), [])
        self.assertEqual(extract('<script type="application/ld+json">bad</script>',
                                 "https://example.org", date(2026, 10, 14)), [])

    def test_query_parameters_preserved_except_tracking(self):
        self.assertEqual(canonical("https://example.org/job?id=2&utm_source=x#top"),
                         "https://example.org/job?id=2")

    def test_rss_to_verified_page(self):
        feed = b'<rss><channel><item><link>https://example.org/empleo/1</link></item></channel></rss>'
        offers, errors = discover(date(2026, 10, 14),
                                  fetch=lambda url: feed if "bing.com" in url else job().encode())
        self.assertEqual(len(offers), 1)
        self.assertEqual(errors, [])

    def test_failed_sources_do_not_mean_no_jobs(self):
        def fail(url):
            raise RuntimeError("network")
        offers, errors = discover(date(2026, 10, 14), fetch=fail)
        self.assertFalse(offers)
        self.assertEqual(len(errors), 5)


class DeliveryTests(unittest.TestCase):
    recipients = ["one@example.org", "two@example.org"]

    def execute(self, store, mail=None, send=True, offers=None):
        return run(store, store.data, offers or [offer()], [], "2026-10-15", self.recipients,
                   gmail=mail, send=send)

    def test_report_and_reservation_before_send(self):
        store = MemoryStore()
        class InspectMail(Mail):
            def send(inner, *args):
                self.assertEqual(store.data["deliveries"][0]["state"], "pending")
                self.assertEqual(len(store.data["runs"]), 1)
                return super().send(*args)
        mail = InspectMail()
        self.execute(store, mail)
        self.assertEqual(store.data["deliveries"][0]["message_id"], "gmail-confirmed-id")
        with self.assertRaises(RuntimeError):
            self.execute(store, mail)
        self.assertEqual(mail.calls, 1)

    def test_reservation_failure_prevents_gmail(self):
        store, mail = MemoryStore(fail_at=2), Mail()
        with self.assertRaises(RuntimeError):
            self.execute(store, mail)
        self.assertEqual(mail.calls, 0)

    def test_timeout_remains_blocked(self):
        store, mail = MemoryStore(), Mail(fail=True)
        with self.assertRaises(RuntimeError):
            self.execute(store, mail)
        self.assertEqual(store.data["deliveries"][0]["state"], "uncertain")
        self.assertTrue(blocked(store.data, "2026-10-15", "one@example.org,two@example.org"))
        self.assertEqual(reconcile(store, store.data, mail), 0)
        self.assertEqual(store.data["deliveries"][0]["state"], "uncertain")

    def test_confirmation_write_failure_keeps_pending(self):
        store, mail = MemoryStore(fail_at=3), Mail()
        with self.assertRaises(RuntimeError):
            self.execute(store, mail)
        self.assertEqual(mail.calls, 1)
        self.assertEqual(store.data["deliveries"][0]["state"], "pending")

    def test_dry_run_and_unchanged_sent_offer_not_repeated(self):
        store = MemoryStore()
        self.execute(store, Mail())
        state, selected, _, _ = prepare(store.data, [offer()], [], "2026-10-16")
        self.assertEqual(selected, [])
        self.assertEqual(len(state["offers"]), 1)
        mail = Mail()
        self.execute(MemoryStore(), mail, send=False)
        self.assertEqual(mail.calls, 0)

    def test_discarded_and_exclusive_requirements_not_selected(self):
        state, _, _, _ = prepare(history(), [offer()], [], "2026-10-15")
        state["offers"][0]["status"] = "descartada"
        self.assertEqual(prepare(state, [offer()], [], "2026-10-16")[1], [])
        item = dict(offer(), requirements="PIR imprescindible")
        self.assertEqual(prepare(history(), [item], [], "2026-10-15")[1], [])

    def test_positive_reconciliation_sets_sent_versions(self):
        store = MemoryStore()
        with self.assertRaises(RuntimeError):
            self.execute(store, Mail(fail=True))
        mail = Mail()
        mail.reconcile = lambda delivery: "reconciled-id"
        self.assertEqual(reconcile(store, store.data, mail), 1)
        self.assertEqual(store.data["deliveries"][0]["state"], "sent")
        self.assertIsNotNone(store.data["offers"][0]["last_sent_fingerprint"])

    def test_report_does_not_leak_home_or_claim_full_search(self):
        item = dict(offer(), requirements="Acudir a Delicias 101")
        report = self.execute(MemoryStore(), send=False, offers=[item])
        self.assertNotIn("Delicias 101", report)
        self.assertIn("Búsqueda parcial", report)

    def test_identical_cross_portal_posting_selected_once(self):
        one = offer()
        two = dict(one, url="https://another.example.org/job")
        state, selected, _, _ = prepare(history(), [one, two], [], "2026-10-15")
        self.assertEqual(len(state["offers"]), 1)
        self.assertEqual(len(state["aliases"]), 2)
        self.assertEqual(len(selected), 1)

    def test_salary_below_target_is_alternative(self):
        low = dict(offer(), salary_raw={"currency": "EUR", "value": {"unitText": "YEAR", "value": 18000}})
        self.assertTrue(rank(low)[0])
        hourly = dict(offer(), salary_raw={"currency": "EUR", "value": {"unitText": "HOUR", "value": 20}})
        self.assertFalse(rank(hourly)[0])

    def test_second_writer_cannot_overwrite_reserved_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "registro.json"
            a, b = LocalStore(path), LocalStore(path)
            a.load()
            a.save(history())
            state_a, state_b = a.load(), b.load()
            mail = Mail()
            run(a, state_a, [offer()], [], "2026-10-15", self.recipients, gmail=mail, send=True)
            with self.assertRaises(Conflict):
                run(b, state_b, [offer()], [], "2026-10-15", self.recipients, gmail=mail, send=True)
            self.assertEqual(mail.calls, 1)


class PersistenceTests(unittest.TestCase):
    def test_schedule_respects_madrid_summer_and_winter(self):
        from empleo.runner import TZ
        self.assertEqual(datetime(2026, 7, 1, 7, tzinfo=timezone.utc).astimezone(TZ).hour, 9)
        self.assertEqual(datetime(2026, 12, 1, 8, tzinfo=timezone.utc).astimezone(TZ).hour, 9)

    def test_wrong_schedule_tick_does_not_touch_store(self):
        from empleo.runner import TZ
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            with patch.dict("os.environ", {"SCHEDULED": "true"}), patch("empleo.__main__.datetime") as clock:
                clock.now.return_value = datetime(2026, 12, 1, 8, tzinfo=TZ)
                self.assertEqual(main(["init", "--state", str(path)]), 0)
            self.assertFalse(path.exists())

    def test_cli_fixture_generates_report_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"offers": [offer()], "errors": []}))
            args = ["run", "--state", str(root / "state.json"), "--fixtures", str(fixture),
                    "--report", str(root / "report.md")]
            with patch("empleo.__main__.Gmail.from_environment") as mail:
                main(args)
                main(args)
                mail.assert_not_called()
            state = json.loads((root / "state.json").read_text())
            self.assertEqual(len(state["deliveries"]), 1)  # Solo el histórico importado.
            self.assertIn("Centro de ejemplo", (root / "report.md").read_text())
            self.assertEqual(len([r for r in state["offers"] if r["data"]["employer"] == "Centro de ejemplo"]), 1)

    def test_local_restart_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            a, b = LocalStore(path), LocalStore(path)
            self.assertIsNone(a.load())
            a.save(history())
            state = b.load()
            changed = a.load()
            changed["events"].append({"event": "new"})
            a.save(changed)
            with self.assertRaises(Conflict):
                b.save(state)
            self.assertEqual(LocalStore(path).load(), changed)

    @patch("empleo.storage.request_json")
    def test_github_compare_and_swap(self, http):
        state = history()
        http.side_effect = [{"sha": "read-sha", "encoding": "base64",
                             "content": base64.b64encode(json.dumps(state).encode()).decode()},
                            {"content": {"sha": "new-sha"}}]
        store = GitHubStore("owner/repo", "not-a-real-token")
        self.assertEqual(store.load(), state)
        store.save(state)
        self.assertEqual(http.call_args.kwargs["data"]["sha"], "read-sha")
        self.assertEqual(store.sha, "new-sha")

    def test_import_is_idempotent_preserves_confirmed_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            main(["init", "--state", str(path)])
            before = path.read_bytes()
            main(["init", "--state", str(path)])
            self.assertEqual(before, path.read_bytes())
            state = json.loads(before)
            self.assertEqual(state["deliveries"][0]["message_id"], "1a139dc4d99a9fbd")

    def test_fixtures_can_never_send(self):
        with self.assertRaises(SystemExit):
            main(["run", "--fixtures", "unused.json", "--send"])


class GmailTests(unittest.TestCase):
    @patch("empleo.gmail.request_json")
    def test_email_payload_has_both_to_and_connected_sender(self, http):
        from email.parser import BytesParser
        from email import policy
        http.side_effect = [{"emailAddress": "sender@example.org"}, {"id": "id"}]
        self.assertEqual(Gmail("fake").send("Informe", "Texto", ["one@example.org", "two@example.org"]), "id")
        raw = http.call_args.kwargs["data"]["raw"]
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(str(message["From"]), "sender@example.org")
        self.assertIn("one@example.org", str(message["To"]))
        self.assertIn("two@example.org", str(message["To"]))

    @patch("empleo.gmail.request_json")
    def test_reconciliation_requires_body_recipient_subject_and_date(self, http):
        from email.message import EmailMessage
        from datetime import datetime
        from empleo.runner import TZ
        msg = EmailMessage()
        msg["To"] = "one@example.org,two@example.org"
        msg["Subject"] = "Informe"
        msg.set_content("contenido")
        record = {"id": "id", "raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(),
                  "internalDate": str(int(datetime(2026, 10, 15, 9, tzinfo=TZ).timestamp()) * 1000)}
        delivery = {"local_date": "2026-10-15", "recipient": "one@example.org,two@example.org",
                    "subject": "Informe", "body_sha256": body_hash("contenido")}
        http.side_effect = [{"messages": [{"id": "id"}]}, record]
        self.assertEqual(Gmail("fake").reconcile(delivery), "id")
        http.side_effect = [{"messages": [{"id": "id"}]}, record]
        delivery["body_sha256"] = body_hash("other content")
        self.assertIsNone(Gmail("fake").reconcile(delivery))


class TransportTests(unittest.TestCase):
    def test_private_network_and_plain_http_rejected(self):
        from empleo.transport import request
        with self.assertRaises(ValueError):
            request("http://example.org")
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                request("https://example.org")


if __name__ == "__main__":
    unittest.main()
