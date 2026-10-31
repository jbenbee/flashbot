def test_pop_activity_on_empty_list_returns_none(running_activities):
    """Regression test: pop_activity used to raise UnboundLocalError on an empty stack."""
    running_activities.pop_all(42)

    assert running_activities.pop_activity(42) is None
    assert not running_activities._lock.locked()


def test_add_and_pop_activity(running_activities):
    running_activities.add_activity(42, 'add_word')

    assert 42 in running_activities.chat_ids
    assert running_activities.current_activity(42) == 'add_word'
    assert running_activities.pop_activity(42) == 'add_word'
    assert not running_activities._lock.locked()


def test_pop_all_clears_activities(running_activities):
    running_activities.add_activity(42, 'add_word')
    running_activities.pop_all(42)

    assert running_activities.current_activity(42) is None
    assert not running_activities._lock.locked()


def test_activities_are_isolated_per_chat(running_activities):
    running_activities.add_activity(1, 'add_word')
    running_activities.add_activity(2, 'other_command')

    running_activities.pop_all(1)

    assert running_activities.current_activity(1) is None
    assert running_activities.current_activity(2) == 'other_command'
