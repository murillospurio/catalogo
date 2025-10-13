from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import os
import json

app = Flask(__name__)
CORS(app)

# === CONFIGURAÇÕES ===
MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-3319944673883642-101218-671fe3f886fe928cac84e01bc31bc20a-1433246274"
POS_EXTERNAL_ID = "GERTEC_MP35P__8701372447323147"  # Device ID correto
ESP32_URL = "http://192.168.5.57/liberar"  # IP e rota do seu ESP32

# === FILA DE PEDIDOS APROVADOS ===
pedidos_aprovados = []

# === FUNÇÃO: CRIAR PAGAMENTO NA MAQUININHA POS ===
def criar_pagamento_maquininha(amount, descricao="Pedido"):
    url = f"https://api.mercadopago.com/point/integration-api/devices/{POS_EXTERNAL_ID}/payment-intents"

    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "amount": float(amount),
        "description": descricao
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response_data = response.json()

        if response.status_code == 201:
            print("Pagamento criado com sucesso!")
            print(json.dumps(response_data, indent=2))
            return response_data
        else:
            print(f"Erro ao criar pagamento: {response.status_code}")
            print(json.dumps(response_data, indent=2))
            return None

    except requests.exceptions.RequestException as e:
        print("Erro de requisição:", e)
        return None

# === FUNÇÃO: VERIFICAR STATUS DO PAGAMENTO ===
def verificar_pagamento(payment_id):
    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers)
    if response.ok:
        return response.json().get("status")
    return None

# === ROTA: RECEBER PEDIDO DO CATÁLOGO ===
@app.route("/pedido", methods=["POST"])
def receber_pedido():
    try:
        data = request.get_json()
        print("📦 Dados recebidos:", data)

        itens = data.get("items", [])
        total = data.get("total", 0)
        order_id = data.get("order_id")

        if not total or not itens:
            return jsonify({"erro": "Pedido inválido"}), 400

        descricao = ", ".join([f"{i['name']} x{i['qty']}" for i in itens])
        print(f"🛒 Pedido recebido: {descricao} | Total R$ {total}")

        amount_cents = int(total * 100)
        pagamento = criar_pagamento_maquininha(amount_cents, descricao)
        if not pagamento or "id" not in pagamento:
            return jsonify({"erro": "Falha ao criar cobrança"}), 500

        payment_id = pagamento["id"]
        print("💳 Pagamento criado, ID:", payment_id)

        for i in range(12):
            status = verificar_pagamento(payment_id)
            print(f"Tentativa {i+1}: status = {status}")

            if status == "approved":
                print("✅ Pagamento aprovado!")

                payload_esp = [{"id": idx + 1, "quantidade": item["qty"]} for idx, item in enumerate(itens)]
                pedidos_aprovados.append({
                    "order_id": order_id,
                    "pedido": payload_esp,
                    "total": total,
                    "liberado": False
                })
                print("📡 Pedido adicionado à fila para ESP32")

                return jsonify({"status": "approved"}), 200

            elif status == "rejected":
                print("❌ Pagamento rejeitado.")
                return jsonify({"status": "rejected"}), 200

            time.sleep(10)

        print("⏱️ Tempo esgotado, pagamento pendente.")
        return jsonify({"status": "pending"}), 200

    except Exception as e:
        print("Erro geral:", e)
        return jsonify({"erro": str(e)}), 500


# === ROTA: WEBHOOK (para notificações automáticas do Mercado Pago) ===
@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    try:
        print("\n📩 Requisição recebida no /webhook")
        print("Headers:", dict(request.headers))
        print("Args:", request.args)
        
        data = {}
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            pass

        print("Body:", data)

        # Tenta extrair o payment_id
        payment_id = request.args.get("id") or data.get("id") or data.get("data", {}).get("id")
        if not payment_id:
            print("⚠️ Nenhum ID de pagamento encontrado.")
            return jsonify({"status": "ignored"}), 200

        print(f"🔎 Consultando pagamento {payment_id}...")

        url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
        headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
        resp = requests.get(url, headers=headers)
        info = resp.json()
        status = info.get("status")
        print(f"💳 Status do pagamento {payment_id}: {status}")

        if status == "approved":
            # Evita duplicar o mesmo pedido
            if payment_id not in [p.get("id") for p in pedidos_aprovados]:
                pedidos_aprovados.append(info)
                print("✅ Pagamento aprovado e adicionado à fila do ESP32.")
            else:
                print("⚠️ Pagamento já existente na fila, ignorando duplicata.")

        return jsonify({"status": "received"}), 200

    except Exception as e:
        print("❌ Erro no webhook:", e)
        return jsonify({"error": str(e)}), 500


# === ROTA: ESP32 CONSULTAR PEDIDOS NÃO LIBERADOS ===
@app.route('/esp_pedido', methods=['GET'])
def esp_pedido():
    global pedidos_aprovados
    print("\n=== ESP SOLICITOU UM PEDIDO ===")
    print("Pedidos aprovados:", pedidos_aprovados)

    if pedidos_aprovados:
        pedido = pedidos_aprovados.pop(0)
        print("➡️ Enviando pedido ao ESP:", pedido)
        return jsonify(pedido)
    else:
        print("⚠️ Nenhum pedido aprovado disponível")
        return jsonify({"status": "vazio"})


# === ROTA: ESP32 MARCAR PEDIDO COMO LIBERADO ===
@app.route("/pedido_liberado", methods=["POST"])
def pedido_liberado():
    data = request.get_json()
    order_id = data.get("order_id")
    for p in pedidos_aprovados:
        if p.get("order_id") == order_id:
            p["liberado"] = True
            print(f"✅ Pedido {order_id} marcado como liberado pelo ESP32")
            return jsonify({"status": "ok"}), 200
    return jsonify({"erro": "pedido não encontrado"}), 404


# === ROTA: HOME ===
@app.route("/", methods=["GET"])
def home():
    return "API Flask conectada à maquininha + ESP32", 200


# === RODAR APP ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
