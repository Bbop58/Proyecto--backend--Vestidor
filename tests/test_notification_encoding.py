import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.notification import NotificationResponse


@pytest.fixture(scope='session', autouse=True)
def setup_test_db():
    # These are pure schema/source tests; do not initialize any database.
    yield


def test_reservation_literals_have_no_mojibake():
    source = Path(__file__).parents[1] / 'app/services/reservation_service.py'
    literals = [node.value for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not [s for s in literals if any(bad in s for bad in ('Ã', 'Â¡', 'â\x8f', 'ðŸ'))]


@pytest.mark.parametrize('kind,title,message,expected_title,expected_message', [
    ('RESERVATION_EXPIRING', 'Reserva Registrada â\x8f³',
     'Tu reserva #RES-1 fue registrada con Ã©xito por Bs 20.00. La reserva vence en 24 horas desde su creación.',
     'Reserva Registrada ⏳',
     'Tu reserva #RES-1 fue registrada con éxito por Bs 20.00. La reserva vence en 24 horas desde su creación.'),
    ('RESERVATION_READY', 'Â¡Tu reserva estÃ¡ lista para recoger! ðŸ“¦',
     'Tu reserva #RES-1 ya estÃ¡ preparada en Sucursal Ñandú. Puedes pasar a retirarla.',
     '¡Tu reserva está lista para recoger! 📦',
     'Tu reserva #RES-1 ya está preparada en Sucursal Ñandú. Puedes pasar a retirarla.'),
    ('GENERAL', 'Texto literal Ã©', 'Cliente escribió Ã¡ y Ñandú',
     'Texto literal Ã©', 'Cliente escribió Ã¡ y Ñandú'),
    ('RESERVATION_READY', '¡Tu reserva está lista para recoger! 📦',
     'Tu reserva #RES-1 ya está preparada en Sucursal Ñandú.',
     '¡Tu reserva está lista para recoger! 📦',
     'Tu reserva #RES-1 ya está preparada en Sucursal Ñandú.'),
])
def test_response_repairs_only_known_legacy_reservation_templates(kind, title, message, expected_title, expected_message):
    row = SimpleNamespace(id=uuid4(), user_id=uuid4(), title=title, message=message,
                          type=kind, reference_id='reservation', is_read=False,
                          created_at=datetime.now(timezone.utc))
    result = NotificationResponse.model_validate(row)
    assert result.title == expected_title
    assert result.message == expected_message
    assert row.title == title and row.message == message  # No database mutation.
    assert result.user_id == row.user_id and result.id == row.id
    assert NotificationResponse.model_validate(result.model_dump()).model_dump() == result.model_dump()
