"""Суммирование суточного трафика из ответа bandwidth-stats.

Эта функция появилась в ветке 3.0.0 ровно потому, что прежний
`stats.get('total', 0)` всегда возвращал 0: верхнеуровневого `total` в ответе
нет и не было, то есть суточная проверка трафика не срабатывала НИ РАЗУ. Первый
её реальный запуск произойдёт на этом деплое, а тестов на неё не было ни одного —
восстановление старого поведения оставляло набор полностью зелёным.
"""

from app.services.traffic_monitoring_service import TrafficMonitoringServiceV2


_sum = TrafficMonitoringServiceV2._sum_bandwidth_stats_bytes


def test_sums_per_node_totals():
    stats = {'series': [{'uuid': 'n-1', 'total': 100}, {'uuid': 'n-2', 'total': 250}]}
    assert _sum(stats) == 350


def test_the_old_top_level_total_is_not_used():
    """Регрессия, ради которой всё и переписывалось.

    Панель отдаёт разбивку по нодам; верхнеуровневый `total` отсутствует, а если
    бы даже пришёл — считать надо по сериям. Чтение `stats['total']` вернуло бы 0
    и проверка снова замолчала бы.
    """
    assert _sum({'series': [{'uuid': 'n-1', 'total': 500}]}) == 500


def test_falls_back_to_daily_breakdown_when_a_node_has_no_total():
    stats = {'series': [{'uuid': 'n-1', 'data': [10, 20, 30]}, {'uuid': 'n-2', 'total': 5}]}
    assert _sum(stats) == 65


def test_accepts_a_flat_list_of_series():
    assert _sum([{'uuid': 'n-1', 'total': 7}, {'uuid': 'n-2', 'total': 3}]) == 10


def test_tolerates_garbage_without_raising():
    assert _sum(None) == 0
    assert _sum('nope') == 0
    assert _sum({}) == 0
    assert _sum({'series': ['junk', None, {}]}) == 0
    assert _sum({'series': [{'total': None, 'data': [None, 4]}]}) == 4


def test_daily_window_is_a_single_calendar_day():
    """Порог назван суточным — окно обязано быть одними сутками.

    Эндпоинт bandwidth-stats принимает только даты, поэтому прежнее окно
    «сегодня-1 … сегодня» складывало ДВОЕ суток и сравнивало с суточным
    порогом: ровный потребитель ниже порога получал ложное «нарушение».
    """
    import ast
    import inspect

    src = inspect.getsource(TrafficMonitoringServiceV2.run_daily_check)
    tree = ast.parse(src.strip())
    assigns = {
        t.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert 'start_date' in assigns and 'end_date' in assigns
    # end_date должен ссылаться на start_date, а не считаться отдельно от now
    assert isinstance(assigns['end_date'], ast.Name) and assigns['end_date'].id == 'start_date', (
        'окно снова разъехалось на два дня'
    )
    assert 'timedelta' not in ast.dump(assigns['start_date']), 'start_date не должен отступать назад на сутки'
