"""Tests de la acción con confirmación del cliente: bloqueo de tarjeta."""

import sqlite3


def chat(client, message, customer):
    response = client.post("/chat", json={"customer_id": customer, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


def card_status(db, last4):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT status FROM cards WHERE last4 = ?", (last4,)).fetchone()[0]


def test_block_requires_confirmation_and_cancel_changes_nothing(client, seeded_db):
    body = chat(client, "Bloquea mi tarjeta de débito, me la robaron", "CLI-004")
    assert body["intent"] == "accion_tarjeta"
    assert body["status"] == "interrupted"
    assert body["interrupt"]["type"] == "action_confirmation"
    assert body["interrupt"]["payload"]["last4"] == "5512"
    assert body["interrupt"]["payload"]["motivo"] == "robo"
    assert card_status(seeded_db, "5512") == "activa"  # nada se ejecuta antes del "sí"

    cancelled = client.post(f"/confirm/{body['thread_id']}", json={"customer_id": "CLI-004", "confirm": False}).json()
    assert cancelled["status"] == "completed"
    assert "no realicé ningún cambio" in cancelled["answer"]
    assert card_status(seeded_db, "5512") == "activa"


def test_confirmed_block_executes_with_audit(client, seeded_db):
    body = chat(client, "quiero bloquear mi tarjeta", "CLI-001")
    result = client.post(f"/confirm/{body['thread_id']}", json={"customer_id": "CLI-001", "confirm": True}).json()
    assert "quedó bloqueada" in result["answer"]
    assert result["citations"] == ["POL-004"]
    assert card_status(seeded_db, "4821") == "bloqueada"
    with sqlite3.connect(seeded_db) as conn:
        audit = conn.execute(
            "SELECT action, reason FROM card_actions WHERE thread_id = ?", (body["thread_id"],)
        ).fetchone()
    assert audit == ("bloqueo", "solicitud del cliente")

    # Idempotencia: pedirlo otra vez no vuelve a ejecutar nada
    again = chat(client, "bloquea mi tarjeta", "CLI-001")
    assert again["status"] == "completed"
    assert "ya está bloqueada" in again["answer"]


def test_other_customer_cannot_confirm(client, seeded_db):
    body = chat(client, "Bloquea mi tarjeta de crédito", "CLI-003")
    forbidden = client.post(f"/confirm/{body['thread_id']}", json={"customer_id": "CLI-002", "confirm": True})
    assert forbidden.status_code == 403
    assert card_status(seeded_db, "7730") == "activa"
    # El dueño sí puede (y se libera la conversación)
    client.post(f"/confirm/{body['thread_id']}", json={"customer_id": "CLI-003", "confirm": False})


def test_customer_without_cards_gets_clear_answer(client):
    body = chat(client, "bloquea mi tarjeta", "CLI-005")
    assert body["status"] == "completed"
    assert "No encontré tarjetas" in body["answer"]


def test_question_about_lost_card_is_still_knowledge(client):
    body = chat(client, "Perdí mi tarjeta, ¿qué hago?", "CLI-002")
    assert body["intent"] == "conocimiento_producto"


def test_confirm_errors(client):
    assert client.post("/confirm/no-existe", json={"customer_id": "CLI-001", "confirm": True}).status_code == 404
    body = chat(client, "¿Cuál es mi saldo?", "CLI-001")
    not_pending = client.post(f"/confirm/{body['thread_id']}", json={"customer_id": "CLI-001", "confirm": True})
    assert not_pending.status_code == 409


def test_action_confirmations_do_not_enter_operator_queue(client):
    body = chat(client, "Bloquea mi tarjeta de débito", "CLI-002")
    queue = client.get("/operator/queue").json()["items"]
    assert body["thread_id"] not in [i["thread_id"] for i in queue]
    client.post(f"/confirm/{body['thread_id']}", json={"customer_id": "CLI-002", "confirm": False})


def test_demo_reset_restores_seed_cards_and_keeps_audit(tmp_path, settings):
    from app.db.sqlite import init_db, restore_seed_cards

    db = tmp_path / "bank.db"
    seed_file = settings.seed_dir / "customers.json"
    init_db(db, seed_file)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE cards SET status = 'bloqueada' WHERE last4 = '4821'")
        conn.execute(
            "INSERT INTO card_actions (card_id, customer_id, action, reason, thread_id, created_at) "
            "SELECT card_id, customer_id, 'bloqueo', 'robo', 't-1', '2026-10-19' FROM cards WHERE last4 = '4821'"
        )
    assert restore_seed_cards(db, seed_file) == 1
    assert card_status(db, "4821") == "activa"
    assert restore_seed_cards(db, seed_file) == 0  # ya está en el estado de la semilla
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM card_actions").fetchone()[0] == 1  # la auditoría queda


def test_init_db_seeds_a_table_added_after_the_database_was_created(tmp_path, settings):
    from app.db.sqlite import init_db

    db = tmp_path / "bank.db"
    seed_file = settings.seed_dir / "customers.json"
    init_db(db, seed_file)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM cards")  # como una base creada antes de la Fase 6
    init_db(db, seed_file)
    assert card_status(db, "4821") == "activa"
