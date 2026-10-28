import memory_eval


def test_the_skeleton_runs_zero_cases_and_exits_cleanly(capsys):
    assert memory_eval.main("dev") == 0

    assert capsys.readouterr().out == "dev: 0 cases, 8 traps defined\n"
