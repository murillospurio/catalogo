from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import json

app = Flask(__name__)
CORS(app)

# === CONFIGURAÇÕES ===
MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-3319944673883642-101218-671fe3f886fe928cac84e01bc31bc20a-1433246274"
POS_EXTERNAL_ID = "GERTEC_MP35P__8701372447323147"
ESP32_URL = "http://192.168.5.57/liberar"

pedidos_aprovados = []
pedidos_pendentes = {}

# Mapeamento de ID para pino do ESP32
ID_MAP = {
    1: 15,
    2: 18,
    3: 19,
    4: 21,
    5: 22,
    6: 23,
    7: 13,
    8: 12,
}

# Fallback caso o item não tenha 'id', usar pelo nome
NOME_MAP = {
    "brahma": 15,
    "skol": 18,
    "coca_cola": 19,
    "coca_cola_zero": 21,
    "sprite": 22,
    "energetico": 23,
    "agua": 13,
    "original": 12,
}

# === FUNÇÕES AUXILIARES ===
def criar_pagamento_maquininha(amount_cents, descricao="Pedido", order_id=None):
    limpar_pagamento_maquininha(POS_EXTERNAL_ID)
    url = f"https://api.mercadopago.com/point/integration-api/devices/{POS_EXTERNAL_ID}/payment-intents"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "transaction_amount": amount_cents / 100,  # converte centavos para reais
        "description": descricao,
        "payment_method_id": "card",
        "external_reference": order_id  # <- referência única
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        if response.status_code == 201:
            print("✅ Pagamento criado na maquininha!")
            print(json.dumps(data, indent=2))
            return data
        else:
            print(f"❌ Erro ao criar pagamento: {response.status_code}")
            print(json.dumps(data, indent=2))
            return None
    except Exception as e:
        print("Erro ao criar pagamento:", e)
        return None


def limpar_pagamento_maquininha(serial_number):
    try:
        url = f"https://api.mercadopago.com/point/integration-api/devices/{serial_number}/payment-intents/cancel"
        headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
        r = requests.post(url, headers=headers)
        print(f"🔄 Limpeza da maquininha: {r.status_code}")
    except Exception as e:
        print(f"Erro ao limpar maquininha: {e}")

def verificar_pagamento(payment_id):
    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    resp = requests.get(url, headers=headers)
    return resp.json() if resp.ok else None

@app.route("/pedido", methods=["POST"])
def receber_pedido():
    try:
        data = request.get_json()
        itens = data.get("items", [])
        total = float(data.get("total", 0))
        order_id = data.get("order_id")

        if not total or not itens or not order_id:
            return jsonify({"erro": "Pedido inválido"}), 400

        descricao = ", ".join([f"{i.get('name','item')} x{i.get('qty',1)}" for i in itens])
        print(f"🛒 Novo pedido {order_id}: {descricao} | Total R$ {total}")

        # Cria pagamento na API MP
        pagamento = criar_pagamento_maquininha(total * 100, descricao, order_id)
        if not pagamento or "id" not in pagamento:
            return jsonify({"erro": "Falha ao criar pagamento"}), 500

        payment_id = str(pagamento["id"])

        # Salva pedido pendente usando order_id como chave
        pedidos_pendentes[order_id] = {
            "order_id": order_id,
            "itens": itens,
            "total": total,
            "status": "pending",
            "payment_id": payment_id
        }

        print(f"📝 Pedido pendente salvo: {order_id} | payment_id={payment_id}")

        return jsonify({"status": "created", "order_id": order_id, "payment_id": payment_id}), 200

    except Exception as e:
        print("Erro ao processar pedido:", e)
        return jsonify({"erro": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    info = request.json or {}
    payment_id = info.get("resource")
    topic = info.get("topic")

    print("📩 Webhook recebido!")
    print(json.dumps(info, indent=2))
    print("🔹 Payment ID:", payment_id)
    print("🔹 Topic:", topic)

    if payment_id and topic == "payment":
        payment_info = verificar_pagamento(payment_id)
        if not payment_info:
            return jsonify({"status": "erro"}), 500

        status = payment_info.get("status")
        order_id = payment_info.get("external_reference")  # <- pega order_id original
        print(f"💳 Pagamento {payment_id} status={status}, order_id={order_id}")

        if status == "approved":
            if order_id in pedidos_pendentes:
                pedido_encontrado = pedidos_pendentes.pop(order_id)
                limpar_pagamento_maquininha(POS_EXTERNAL_ID)

                payload_esp = []
                for item in pedido_encontrado["itens"]:
                    pid = item.get("id") or NOME_MAP.get(item.get("name"), 1)
                    payload_esp.append({"id": pid, "quantidade": item.get("qty",1)})

                pedidos_aprovados.append({
                    "order_id": order_id,
                    "pedido": payload_esp,
                    "total": pedido_encontrado["total"],
                    "liberado": False
                })

                print(f"✅ Pedido {order_id} aprovado e enviado para fila do ESP32.")
                print("➡️ Payload ESP32:", json.dumps(payload_esp, indent=2))
            else:
                print("⚠️ Payment aprovado, mas pedido não encontrado nos pendentes.")

    return jsonify({"status": "ok"})

@app.route("/esp_pedido", methods=["GET"])
def esp_pedido():
    """
    Rota que o ESP32 consulta para pegar pedidos aprovados
    """
    pedidos_para_esp = [p for p in pedidos_aprovados if not p["liberado"]]
    for p in pedidos_para_esp:
        p["liberado"] = True
    return jsonify(pedidos_para_esp), 200

# ================= MAIN =================
if __name__ == "__main__":
    app.run(debug=True, port=5000)