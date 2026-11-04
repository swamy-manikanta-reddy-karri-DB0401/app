"""Offline checks of saved chat history: written in batches by one writer, readable at once, never resurrected."""
from __future__ import annotations

import sys
import time

import chats

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class Fake:
    def __init__(self, slow=0.0):
        self.statements, self.rows, self.slow = [], [], slow

    def run(self, sql, params=None):
        self.statements.append(sql)
        p = {x["name"]: x.get("value") for x in params or []}
        if sql.startswith("INSERT"):
            time.sleep(self.slow)
            i = 0
            while "cid%d" % i in p:
                self.rows.append([p["i%d" % i], p["r%d" % i], p["c%d" % i], p["m%d" % i], p["ep%d" % i], p["u%d" % i], p["cid%d" % i]])
                i += 1
            return []
        if sql.startswith("SELECT idx"):
            return [r[:5] for r in self.rows if r[5] == p["u"] and r[6] == p["cid"]]
        if sql.startswith("DELETE"):
            self.rows = [r for r in self.rows if not (r[5] == p["u"] and r[6] == p["cid"])]
        return []


def fresh(slow=0.0):
    f = Fake(slow)
    chats.store.run = f.run
    chats.QUALIFIED = "`c`.`s`.`t`"
    chats.enabled = lambda: True
    chats.ensure_table = lambda: None
    return f


CID = "0" * 8 + "-0000-4000-8000-" + "0" * 12


@case("many replies are written in a few batched statements, not one each")
def _():
    f = fresh()
    for n in range(30):
        chats.save("a@x.io", CID.replace("0", "1", 1) if n % 2 else CID, "ep", n * 2, [{"role": "user", "text": "q%d" % n}, {"role": "assistant", "text": "a"}])
    chats.flush()
    inserts = [s for s in f.statements if s.startswith("INSERT")]
    assert len(f.rows) == 60 and len(inserts) <= 3, (len(f.rows), len(inserts))


@case("a conversation opened before its rows are written still shows them, in order, once")
def _():
    f = fresh(slow=0.5)
    chats.save("a@x.io", CID, "ep", 0, [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hi"}])
    got = chats.get_chat("a@x.io", CID)
    assert [m["text"] for m in got] == ["hello", "hi"]
    assert chats.get_chat("b@x.io", CID) == []  # never someone else's
    chats.flush()
    assert [m["text"] for m in chats.get_chat("a@x.io", CID)] == ["hello", "hi"]


@case("deleting a conversation while its rows wait means they are never written back")
def _():
    f = fresh(slow=0.3)
    chats.save("a@x.io", CID, "ep", 0, [{"role": "user", "text": "first"}])
    time.sleep(0.05)
    chats.save("a@x.io", CID, "ep", 1, [{"role": "assistant", "text": "second"}])
    chats.delete_chat("a@x.io", CID)
    chats.flush()
    time.sleep(0.6)
    assert chats.get_chat("a@x.io", CID) == [] and not [r for r in f.rows if r[6] == CID]


@case("a delete that arrives while that conversation is being written waits, so the rows cannot come back")
def _():
    f = fresh(slow=0.6)
    chats.save("a@x.io", CID, "ep", 0, [{"role": "user", "text": "in flight"}])
    time.sleep(chats.FLUSH_SECONDS + 0.3)  # the writer has taken the row and is writing it now
    chats.delete_chat("a@x.io", CID)
    chats.flush()
    assert not [r for r in f.rows if r[6] == CID]


if __name__ == "__main__":
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  " + name + "\n        " + type(exc).__name__ + ": " + str(exc)[:300])
    print("--- %d passed, %d failed ---" % (len(CASES) - failed, failed))
    sys.exit(1 if failed else 0)
