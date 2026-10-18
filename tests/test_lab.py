import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from http.server import ThreadingHTTPServer
from hermes_lab.core import normalize, metrics, replay, risk_estimate, parse_file, ValidationError, session_for, instant
from hermes_lab.journal import Journal
from hermes_lab.server import make_handler


def row(eid="a",pnl="100.00",at="2026-10-01T15:00:00Z",**kw):
    return {"event_id":eid,"closed_at":at,"pnl_net_usd":pnl,"account_id":"TEST",**kw}


class ImportTests(unittest.TestCase):
    def test_reject_missing_timezone(self):
        with self.assertRaises(ValidationError):normalize([row(at="2026-10-01T15:00:00")])

    def test_reject_missing_stable_id(self):
        with self.assertRaises(ValidationError):normalize([row(eid="")])

    def test_reject_nonfinite_values(self):
        for val in ["NaN","Infinity","-Infinity",True]:
            with self.assertRaises(ValidationError):normalize([row(pnl=val)])

    def test_gross_requires_fees(self):
        with self.assertRaisesRegex(ValidationError,"frais requis"):normalize([{"event_id":"x","closed_at":"2026-10-01T15:00:00Z","pnl_gross_usd":100,"account_id":"TEST"}])

    def test_net_fees_are_not_subtracted_twice(self):
        events=normalize([row(pnl="96",fees_usd="4")])
        self.assertEqual(metrics(events,50000)["net_pnl"],96)

    def test_gross_conversion(self):
        r=row();r.pop("pnl_net_usd");r.update(pnl_gross_usd="100",fees_usd="4")
        self.assertEqual(normalize([r])[0]["pnl_net_usd"],"96.00")

    def test_inconsistent_net_gross_rejected(self):
        with self.assertRaises(ValidationError):normalize([row(pnl="99",pnl_gross_usd="100",fees_usd="4")])

    def test_multiple_accounts_rejected(self):
        with self.assertRaises(ValidationError):normalize([row(),row(eid="b",account_id="OTHER")])

    def test_semicolon_french_csv(self):
        data="event_id;closed_at;pnl_net_usd\na;2026-10-01T15:00:00Z;12,30\n"
        self.assertEqual(normalize(parse_file(data,"f.csv"),account="TEST")[0]["pnl_net_usd"],"12.30")

    def test_missing_account_is_rejected(self):
        r=row();r.pop("account_id")
        with self.assertRaisesRegex(ValidationError,"compte obligatoire"):normalize([r])

    def test_session_format_is_strict(self):
        with self.assertRaises(ValidationError):normalize([row(session_date="20261001")])

    def test_ambiguous_thousands_rejected(self):
        with self.assertRaises(ValidationError):normalize([row(pnl="1,234")])

    def test_unknown_event_type_rejected(self):
        with self.assertRaises(ValidationError):normalize([row(event_type="open")])

    def test_conflicting_session_dates_rejected(self):
        with self.assertRaises(ValidationError):normalize([row(session_date="2026-10-02"),row(eid="b",at="2026-10-02T15:00:00Z",session_date="2026-10-01")])

    def test_mapping_custom_columns(self):
        r=normalize([{"id":"a","when":"2026-10-01T15:00:00Z","net":"25"}],{"event_id":"id","closed_at":"when","pnl_net_usd":"net"},"X")
        self.assertEqual(r[0]["account_id"],"X")


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.j=Journal(Path(self.tmp.name)/"journal.db")

    def tearDown(self):self.tmp.cleanup()

    def test_duplicate_is_idempotent(self):
        e=normalize([row()]);self.j.ingest(e,"one.csv")
        self.assertEqual(self.j.ingest(e,"two.csv"),{"inserted":0,"duplicates":1})
        self.assertEqual(len(self.j.events()),1)

    def test_conflict_rolls_back_entire_import(self):
        self.j.ingest(normalize([row(eid="z")]),"one.csv")
        with self.assertRaises(ValidationError):self.j.ingest(normalize([row(eid="a"),row(eid="z",pnl=900)]),"bad.csv")
        self.assertEqual([e["event_id"] for e in self.j.events()],["z"])

    def test_cross_account_rejected_without_mutation(self):
        self.j.ingest(normalize([row()]),"one")
        with self.assertRaises(ValidationError):self.j.ingest(normalize([row(eid="b",account_id="OTHER")]),"two")
        self.assertEqual(len(self.j.events()),1)

    def test_roundtrip_export_is_duplicate(self):
        events=normalize([row()]);self.j.ingest(events,"one")
        self.assertEqual(self.j.ingest(normalize(self.j.events()),"export")["duplicates"],1)

    def test_notes_persist_and_backup_preserves(self):
        self.j.ingest(normalize([row()]),"one");self.j.save_note("a","Revoir le setup")
        target=Path(self.tmp.name)/"copy.db";self.j.backup(target)
        copy=Journal(target)
        self.assertEqual(copy.notes()["a"],"Revoir le setup")
        with self.assertRaises(ValidationError):self.j.backup(target)


class MetricTests(unittest.TestCase):
    def test_drawdown_includes_initial_capital(self):
        events=normalize([row(pnl=-100),row(eid="b",pnl=50,at="2026-10-02T15:00:00Z")])
        m=metrics(events,50000)
        self.assertEqual(m["max_drawdown_closed"],100)
        self.assertEqual(m["net_pnl"],-50)
        self.assertEqual(m["expectancy"],-25)

    def test_drawdown_from_new_peak(self):
        e=normalize([row(pnl=100),row(eid="b",pnl=-150,at="2026-10-02T15:00:00Z")])
        self.assertEqual(metrics(e,50000)["max_drawdown_closed"],150)

    def test_withdrawal_does_not_become_trading_loss(self):
        e=normalize([row(),row(eid="w",event_type="withdrawal",withdrawal_usd=50,at="2026-10-02T15:00:00Z")])
        m=metrics(e,50000)
        self.assertEqual(m["net_pnl"],100);self.assertEqual(m["cash_balance"],50050)
        self.assertEqual(m["max_drawdown_closed"],0)

    def test_no_losses_pf_is_unknown_not_999(self):
        self.assertIsNone(metrics(normalize([row()]),50000)["profit_factor"])


class ReplayTests(unittest.TestCase):
    def test_floor_does_not_follow_intraday_peak(self):
        e=normalize([row(pnl=1000),row(eid="b",pnl=-500,at="2026-10-01T16:00:00Z")])
        r=replay(e,{"size":50000})
        self.assertEqual([x["floor"] for x in r["timeline"]],[48000,48000])
        self.assertEqual(r["floor_next_session"],48500)

    def test_breach_at_equality_is_latched(self):
        e=normalize([row(pnl=-2000),row(eid="b",pnl=4000,at="2026-10-02T15:00:00Z")])
        self.assertTrue(replay(e,{"size":50000})["breached_on_closed_events"])

    def test_floor_cap(self):
        r=replay(normalize([row(pnl=4000)]),{"size":50000})
        self.assertEqual(r["floor_next_session"],50100)

    def test_payout_locks_floor_and_can_breach(self):
        e=normalize([row(pnl=1000),row(eid="w",event_type="withdrawal",withdrawal_usd=950,at="2026-10-02T15:00:00Z")])
        r=replay(e,{"size":50000,"stage":"funded"})
        self.assertTrue(r["breached_on_closed_events"])
        self.assertEqual(r["floor_next_session"],50100)

    def test_payout_is_not_dll_loss(self):
        e=normalize([row(pnl=1000),row(eid="w",event_type="withdrawal",withdrawal_usd=500,at="2026-10-01T16:00:00Z")])
        self.assertFalse(replay(e,{"size":50000,"stage":"funded","daily_loss_limit":100})["daily_loss_locked"])

    def test_dll_stays_locked_after_recovery(self):
        e=normalize([row(pnl=-100),row(eid="b",pnl=200,at="2026-10-01T16:00:00Z")])
        r=replay(e,{"size":50000,"daily_loss_limit":100})
        self.assertTrue(r["daily_loss_locked"])
        self.assertIn("AFTER_DLL",[v["kind"] for v in r["violations"]])

    def test_scaling_updates_at_session_end(self):
        e=normalize([row(pnl=1200),row(eid="b",pnl=100,at="2026-10-02T15:00:00Z")])
        r=replay(e,{"size":50000,"stage":"funded"})
        self.assertEqual([x["max_micros_during_session"] for x in r["timeline"]],[20,30])

    def test_partial_history_requires_floor(self):
        with self.assertRaises(ValidationError):replay([],{"size":50000,"initial_balance":51000})

    def test_floor_cannot_be_looser_than_initial_limit(self):
        with self.assertRaises(ValidationError):replay([],{"size":50000,"initial_floor":47000})

    def test_timezone_dst_rollover(self):
        self.assertEqual(session_for(instant("2026-07-01T22:00:00Z")),"2026-07-02")
        self.assertEqual(session_for(instant("2026-12-01T22:00:00Z")),"2026-12-01")


class RiskTests(unittest.TestCase):
    def params(self):return dict(equity=50000,floor=48000,buffer=200,daily_remaining=150,reserved_risk=0,risk_cap=50,stop_ticks=32,tick_value="1.25",fees=4,slippage_ticks=2,max_contracts=10,snapshot_age_seconds=0)

    def test_fees_and_slippage_in_size(self):
        p=self.params();p["risk_cap"]=90
        r=risk_estimate(p)
        self.assertEqual(r["risk_per_contract"],46.5)
        self.assertEqual(r["quantity_estimate"],1)
        self.assertFalse(r["execution_authorized"])

    def test_pending_risk_reduces_available_budget(self):
        p=self.params();p.update(reserved_risk=130,risk_cap=200)
        self.assertEqual(risk_estimate(p)["quantity_estimate"],0)

    def test_stale_snapshot_blocks(self):
        p=self.params();p["snapshot_age_seconds"]=6
        self.assertEqual(risk_estimate(p)["quantity_estimate"],0)

    def test_no_negative_or_fractional_size(self):
        p=self.params();p["max_contracts"]="0.5"
        with self.assertRaises(ValidationError):risk_estimate(p)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.j=Journal(Path(self.tmp.name)/"http.db")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(self.j,"test-token"))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def req(self,path,body=None,**headers):
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={"X-Lab-Token":"test-token","Content-Type":"application/json",**headers})
        return urllib.request.urlopen(req)

    def test_live_routes_absent(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.req('/api/orders',{})
        self.assertEqual(e.exception.code,404)

    def test_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.base+'/api/state')
        self.assertEqual(e.exception.code,403)

    def test_foreign_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.req('/api/state',Origin='https://attacker.example')
        self.assertEqual(e.exception.code,403)

    def test_example_is_not_written(self):
        with self.req('/api/analyze',{"example":True,"profile":{"size":50000}}) as r:
            body=json.load(r)
        self.assertTrue(body["example"]);self.assertEqual(len(body["events"]),12)
        self.assertEqual(self.j.events(),[])

    def test_end_to_end_import_and_report(self):
        text='event_id,closed_at,pnl_net_usd,fees_usd,account_id\na,2026-10-01T15:00:00Z,100,4,TEST\nb,2026-10-02T15:00:00Z,-40,4,TEST\n'
        with self.req('/api/import',{"filename":"trades.csv","text":text}) as r:self.assertEqual(json.load(r)["inserted"],2)
        with self.req('/api/analyze',{"profile":{"size":50000}}) as r:result=json.load(r)
        self.assertEqual(result["metrics"]["net_pnl"],60)
        self.assertEqual(result["metrics"]["max_drawdown_closed"],40)


if __name__=="__main__":unittest.main()
