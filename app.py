from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import os
import json

app = Flask(__name__)
CORS(app)

MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-3319944673883642-101218-671fe3f886fe928cac84e01bc31bc20a-1433246274"
POS_EXTERNAL_ID = "GERTEC_MP35P__8701372447323147"
ESP32_URL = "http://192.168.5.57/liberar"

pedidos_aprovados = []
pedidos_pendentes = {}

# === Criar pastas locais ===
os.makedirs("pedidos_aprovados", exist_ok=True)
os.makedirs("pedidos_pendentes", exist_ok=True)


# === FUNÇÃO: CRIAR PAGAMENTO NA MAQUININHA ===
def criar_pagamento_maquininha(amount, descricao="Pedido", order_id=None, forma_pagamento="debito"):
    if order_id in pedidos_pendentes:
        print(f"⚠️ Pedido {order_id} já possui cobrança pendente. Ignorando nova criação.")
        return None

    url = f"https://api.mercadopago.com/point/integration-api/devices/{POS_EXTERNAL_ID}/payment-intents"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # === Ajusta o método conforme a forma escolhida ===
    if forma_pagamento == "pix":
        payment_type = "PIX"
    elif forma_pagamento == "credito":
        payment_type = "CREDIT_CARD"
    else:
        payment_type = "DEBIT_CARD"

    payload = {
        "amount": int(float(amount) * 100),
        "description": f"{descricao} - {forma_pagamento.upper()}",
        "payment": {
            "type": payment_type
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response_data = response.json()
        if response.status_code == 201:
            print(f"✅ Pagamento criado na maquininha ({forma_pagamento}) para o pedido {order_id}!")
            print(json.dumps(response_data, indent=2))
            return response_data
        else:
            print(f"❌ Erro ao criar pagamento ({response.status_code}):")
            print(json.dumps(response_data, indent=2))
            return None
    except Exception as e:
        print("Erro ao criar pagamento:", e)
        return None


# === FUNÇÃO: LIMPAR PAGAMENTO PENDENTE ===
def limpar_pagamento_maquininha(serial_number):
    try:
        url = f"https://api.mercadopago.com/point/integration-api/devices/{serial_number}/payment-intents/cancel"
        headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
        r = requests.post(url, headers=headers)
        print(f"🔄 Limpeza da maquininha: {r.status_code}")
    except Exception as e:
        print(f"Erro ao limpar maquininha: {e}")


# === FUNÇÃO: VERIFICAR STATUS ===
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
        forma_pagamento = data.get("forma_pagamento", "debito")  # 👈 NOVO

        if not total or not itens:
            return jsonify({"erro": "Pedido inválido"}), 400

        descricao = ", ".join([f"{i['name']} x{i['qty']}" for i in itens])
        print(f"🛒 Novo pedido {order_id}: {descricao} | Total R$ {total} | Forma: {forma_pagamento.upper()}")

        pagamento = criar_pagamento_maquininha(total, descricao, order_id, forma_pagamento)
        if not pagamento:
            return jsonify({"erro": "Falha ao criar pagamento"}), 500

        pedidos_pendentes[order_id] = {
            "order_id": order_id,
            "itens": itens,
            "total": total,
            "status": "pending",
            "forma_pagamento": forma_pagamento,
            "payment_id": pagamento.get("id")
        }

        return jsonify({"status": "created", "order_id": order_id, "forma_pagamento": forma_pagamento}), 200

    except Exception as e:
        print("Erro ao processar pedido:", e)
        return jsonify({"erro": str(e)}), 500


# === ROTA: WEBHOOK ===
@app.route("/webhook", methods=["POST"])
def webhook():
    info = request.json or {}
    payment_id = info.get("data", {}).get("id") or request.args.get("id") or info.get("resource")
    topic = info.get("topic") or request.args.get("topic")

    print("📩 Webhook recebido!")
    print(json.dumps(info, indent=2))
    print("🔹 Payment ID:", payment_id)
    print("🔹 Topic:", topic)

    if payment_id:
        payment_info = verificar_pagamento(payment_id)
        status = payment_info.get("status") if payment_info else None
        print(f"💳 Pagamento {payment_id} status={status}")

        if status == "approved":
            if pedidos_pendentes:
                order_ref, pedido = pedidos_pendentes.popitem()

                try:
                    limpar_pagamento_maquininha(POS_EXTERNAL_ID)
                    print(f"🧹 Pagamento na maquininha limpo após aprovação de {order_ref}")
                except Exception as e:
                    print("⚠️ Falha ao limpar maquininha:", e)

                payload_esp = []
                for item in pedido["itens"]:
                    prod_id = item.get("id")
                    payload_esp.append({"id": prod_id, "quantidade": item["qty"]})

                pedidos_aprovados.append({
                    "order_id": order_ref,
                    "pedido": payload_esp,
                    "total": pedido["total"],
                    "forma_pagamento": pedido.get("forma_pagamento", "desconhecida"),
                    "liberado": False
                })

                print(f"✅ Pedido {order_ref} aprovado ({pedido.get('forma_pagamento').upper()}) e enviado ao ESP32.")

                try:
                    r = requests.get(ESP32_URL, timeout=5)
                    print("📡 ESP32 notificado:", r.status_code)
                except Exception as e:
                    print("⚠️ Falha ao notificar ESP32:", e)

    return jsonify({"status": "ok"})


@app.route("/esp_pedido", methods=["GET"])
def esp_pedido():
    print("\n📲 ESP consultou pedidos...")
    try:
        if pedidos_aprovados:
            pedido = pedidos_aprovados.pop(0)
            print("➡️ Enviando pedido:", pedido)
            return jsonify(pedido)
    except Exception as e:
        print("⚠️ Erro ao enviar pedido:", e)

    print("⚠️ Nenhum item para liberar.")
    return jsonify({"status": "vazio"}), 200


@app.route("/", methods=["GET"])
def home():
    return "✅ API Flask + Mercado Pago + ESP32 ativa!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
