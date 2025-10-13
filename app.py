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
POS_EXTERNAL_ID = "Mu01"  # ID configurado na maquininha (POS)
ESP32_URL = "http://192.168.5.57/liberar"  # IP e rota do seu ESP32 (não usado no pull do ESP)

# === FILA DE PEDIDOS APROVADOS ===
pedidos_aprovados = []

def criar_pagamento_maquininha(amount, external_pos_id, payment_type="credit_card"):
    """
    Cria uma transação no POS do Mercado Pago.
    amount: float, valor da cobrança
    external_pos_id: str, identificador da maquininha
    payment_type: 'credit_card' ou 'debit_card'
    """
    url = "https://api.mercadopago.com/v1/point/transactions"

    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "transaction_amount": float(amount),  # deve ser número decimal
        "external_pos_id": external_pos_id,  # identificador da maquininha
        "payment_type": payment_type
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
# === Consultar status do pagamento ===
def verificar_pagamento(payment_id):
    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers)
    if response.ok:
        return response.json().get("status")
    return None

# === Rota que recebe o pedido do catálogo ===
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

        # Cria descrição do pedido
        descricao = ", ".join([f"{i['name']} x{i['qty']}" for i in itens])
        print(f"🛒 Pedido recebido: {descricao} | Total R$ {total}")

        # === Envia cobrança para maquininha ===
        pagamento = criar_pagamento_maquininha(total, descricao)
        if not pagamento or "id" not in pagamento:
            return jsonify({"erro": "Falha ao criar cobrança"}), 500

        payment_id = pagamento["id"]
        print("💳 Pagamento criado, ID:", payment_id)

        # === Espera aprovação do pagamento (até 2 minutos) ===
        for i in range(12):
            status = verificar_pagamento(payment_id)
            print(f"Tentativa {i+1}: status = {status}")

            if status == "approved":
                print("✅ Pagamento aprovado!")

                # Adiciona à fila de pedidos para ESP32
                payload_esp = [{"id": idx + 1, "quantidade": i["qty"]} for idx, i in enumerate(itens)]
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

# === Rota para ESP32 consultar pedidos não liberados ===
@app.route("/esp_pedido", methods=["GET"])
def esp_pedido():
    for p in pedidos_aprovados:
        if not p["liberado"]:
            return jsonify(p), 200
    return jsonify({"pedido": []}), 200

# === Rota para ESP32 marcar pedido como liberado ===
@app.route("/pedido_liberado", methods=["POST"])
def pedido_liberado():
    data = request.get_json()
    order_id = data.get("order_id")
    for p in pedidos_aprovados:
        if p["order_id"] == order_id:
            p["liberado"] = True
            print(f"✅ Pedido {order_id} marcado como liberado pelo ESP32")
            return jsonify({"status": "ok"}), 200
    return jsonify({"erro": "pedido não encontrado"}), 404

@app.route("/", methods=["GET"])
def home():
    return "API Flask conectada à maquininha + ESP32", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
