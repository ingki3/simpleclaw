from simpleclaw.agent.commands import parse_goal_command


def test_parse_goal_help_forms():
    assert parse_goal_command("/goal").action == "help"
    assert parse_goal_command("/goal help").action == "help"


def test_parse_goal_start():
    cmd = parse_goal_command("/goal SimpleClaw 로그를 확인해 원인을 찾아줘")

    assert cmd.action == "start"
    assert cmd.objective == "SimpleClaw 로그를 확인해 원인을 찾아줘"


def test_parse_goal_reserved_controls():
    assert parse_goal_command("/goal status").action == "unsupported"
    assert parse_goal_command("/goal cancel").action == "unsupported"
    assert parse_goal_command("/goal clear").action == "unsupported"
    assert parse_goal_command("/goal list").action == "unsupported"


def test_parse_goal_non_goal_returns_none():
    assert parse_goal_command("/cron list") is None
    assert parse_goal_command("/morning-briefing") is None
    assert parse_goal_command("/goalkeeper") is None
    assert parse_goal_command("그냥 대화") is None
