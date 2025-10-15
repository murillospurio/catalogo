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
def criar_pagamento_maquininha(amount, descricao="Pedido", order_id=None):
    limpar_pagamento_maquininha(POS_EXTERNAL_ID)
    url = f"https://api.mercadopago.com/point/integration-api/devices/{POS_EXTERNAL_ID}/payment-intents"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"amount": float(amount), "description": descricao}

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

# === ROTA: RECEBER PEDIDO DO CATÁLOGO ===
@app.route("/pedido", methods=["POST"])
def receber_pedido():
    try:
        data = request.get_json()
        itens = data.get("items", [])
        total = float(data.get("total", 0))
        order_id = data.get("order_id")

        if not total or not itens:
            return jsonify({"erro": "Pedido inválido"}), 400

        descricao = ", ".join([f"{i.get('name','item')} x{i.get('qty',1)}" for i in itens])
        print(f"🛒 Novo pedido {order_id}: {descricao} | Total R$ {total}")

        # Cria pagamento
        pagamento = criar_pagamento_maquininha(total * 100, descricao, order_id)
        if not pagamento or "id" not in pagamento:
            return jsonify({"erro": "Falha ao criar pagamento"}), 500

        # ⚠️ Pega o ID real do pagamento (único por transação)
        payment_info = verificar_pagamento(pagamento["id"])
        real_payment_id = str(payment_info.get("id")) if payment_info else str(pagamento["id"])

        # Salva pedido pendente usando o real_payment_id como chave
        pedidos_pendentes[real_payment_id] = {
            "order_id": order_id,
            "itens": itens,
            "total": total,
            "status": "pending",
            "payment_id": real_payment_id
        }

        print(f"📝 Pedido pendente salvo: {order_id} | payment_id={real_payment_id}")

        return jsonify({"status": "created", "order_id": order_id, "payment_id": real_payment_id}), 200

    except Exception as e:
        print("Erro ao processar pedido:", e)
        return jsonify({"erro": str(e)}), 500

# === ROTA: WEBHOOK DE PAGAMENTO ===
@app.route("/webhook", methods=["POST"])
def webhook():
    info = request.json or {}
    payment_id = str(info.get("data", {}).get("id") or info.get("resource"))
    topic = info.get("topic")

    print("📩 Webhook recebido!")
    print(json.dumps(info, indent=2))
    print("🔹 Payment ID:", payment_id)
    print("🔹 Topic:", topic)

    if payment_id:
        payment_info = verificar_pagamento(payment_id)
        status = payment_info.get("status") if payment_info else None
        print(f"💳 Pagamento {payment_id} status={status}")

        if status == "approved":
            # Procura pedido pelo payment_id diretamente
            pedido_encontrado = pedidos_pendentes.pop(payment_id, None)

            if pedido_encontrado:
                limpar_pagamento_maquininha(POS_EXTERNAL_ID)

                # Cria payload para ESP32 usando ID_MAP ou NOME_MAP
                payload_esp = []
                for item in pedido_encontrado["itens"]:
                    pid = item.get("id") or NOME_MAP.get(item.get("name"), 1)
                    payload_esp.append({"id": pid, "quantidade": item.get("qty",1)})

                pedidos_aprovados.append({
                    "order_id": pedido_encontrado["order_id"],
                    "pedido": payload_esp,
                    "total": pedido_encontrado["total"],
                    "liberado": False
                })

                print(f"✅ Pedido {pedido_encontrado['order_id']} aprovado e enviado para fila do ESP32.")
                print("➡️ Payload ESP32:", json.dumps(payload_esp, indent=2))
            else:
                print("⚠️ Payment aprovado, mas pedido não encontrado nos pendentes.")

    return jsonify({"status": "ok"})


# === ROTA: ESP CONSULTA PEDIDOS ===
@app.route("/esp_pedido", methods=["GET"])
def esp_pedido():
    print("\n📲 ESP consultou pedidos...")
    if pedidos_aprovados:
        pedido = pedidos_aprovados.pop(0)
        print("➡️ Enviando pedido:", pedido)
        return jsonify(pedido)

    print("⚠️ Nenhum item para liberar.")
    return jsonify({"status": "vazio"}), 200

# === ROTA: HOME ===
@app.route("/", methods=["GET"])
def home():
    return "✅ API Flask + Mercado Pago + ESP32 ativa!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
