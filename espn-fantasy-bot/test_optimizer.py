from espn_bot import optimize, plan_moves, BENCH, IR

def P(i, slot, elig, team, val, inj="ACTIVE"):
    return {"id": i, "name": f"p{i}", "slot": slot, "eligible": set(elig),
            "pro_team": team, "value": val, "injury": inj}

def test_basic():
    # slots: PG, C, UTIL
    slots = [0, 4, 11]
    roster = [
        P(1, BENCH, [0, 5, 11, 12], 1, 40),       # PG playing
        P(2, 0,     [0, 5, 11, 12], 2, 50),       # PG no game -> bench
        P(3, BENCH, [4, 9, 10, 11, 12], 1, 30),   # C playing
        P(4, 11,    [3, 4, 11, 12, 13], 1, 60, "OUT"),  # OUT -> IR with --use-ir
        P(5, BENCH, [2, 3, 11, 12], 1, 20),       # wing playing -> UTIL
    ]
    t = optimize(roster, slots, {1}, use_ir=True, ir_slots=1)
    assert t == {1: 0, 2: BENCH, 3: 4, 4: IR, 5: 11}, t
    assert len(plan_moves(roster, t)) == 5

def test_augmenting_path():
    # PG-only player must take PG, so the G-flex guard moves to UTIL.
    slots = [0, 11]
    roster = [P(1, BENCH, [0, 5, 11, 12], 1, 50), P(2, BENCH, [0, 12], 1, 40)]
    t = optimize(roster, slots, {1}, False, 0)
    assert t == {1: 11, 2: 0}, t

def test_no_churn():
    slots = [11, 11]
    roster = [P(1, 11, [11, 12], 1, 10), P(2, 11, [11, 12], 1, 20)]
    assert plan_moves(roster, optimize(roster, slots, {1}, False, 0)) == []

for f in (test_basic, test_augmenting_path, test_no_churn):
    f(); print("ok", f.__name__)
