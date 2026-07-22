"""Смоук-тест модели PlategaSubscription: импорт и колонки таблицы."""

from app.database.models import PlategaSubscription


def test_model_table_and_columns():
    assert PlategaSubscription.__tablename__ == 'platega_subscriptions'
    cols = set(PlategaSubscription.__table__.columns.keys())
    assert {'platega_subscription_id', 'interval', 'charge_days', 'amount_kopeks', 'status'} <= cols
