from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import os 

app = Flask(__name__)
CORS(app)

# === CONFIGURAÇÕES ===
MERCADO_PAGO_ACCESS_TOKEN = "APP_USR-3319944673883642-101218-671fe3f886fe928cac84e01bc31bc20a-1433246274"
POS_EXTERNAL_ID = "Mu01"  # ID configurado na maquininha (POS)
ESP32_URL = "http://192.168.5.57/liberar"  # IP e rota do seu ESP32

# === Criar pagamento direto para a maquininha ===
def criar_pagamento_maquininha(valor_total, descricao):
    url = "https://api.mercadopago.com/point/integrations/transactions"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "external_pos_id": POS_EXTERNAL_ID,
        "description": descricao,
        "payment": {
            "amount": valor_total,
            "type": "credit_card"  # Pode ser "credit_card" ou "debit_card"
        }
    }

    response = requests.post(url, headers=headers, json=data)
    print("🔸 Enviando cobrança para maquininha:", response.text)
    return response.json() if response.ok else None


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
        itens = data.get("itens", [])
        total = data.get("total")

        if not total:
            return jsonify({"erro": "Valor total ausente"}), 400

        descricao = ", ".join([f"{i['nome']} x{i['quantidade']}" for i in itens])
        print(f"🛒 Pedido recebido: {descricao} | Total R$ {total}")

        # === Envia cobrança para maquininha ===
        pagamento = criar_pagamento_maquininha(total, descricao)
        if not pagamento or "id" not in pagamento:
            return jsonify({"erro": "Falha ao criar cobrança"}), 500

        payment_id = pagamento["id"]
        print("💳 Pagamento criado, ID:", payment_id)

        # === Espera aprovação do pagamento ===
        for i in range(12):  # 2 minutos (12 tentativas de 10s)
            status = verificar_pagamento(payment_id)
            print(f"Tentativa {i+1}: status = {status}")

            if status == "approved":
                print("✅ Pagamento aprovado!")

                # === Envia itens para o ESP32 liberar ===
                payload_esp = {"itens": itens, "total": total}
                try:
                    resp = requests.post(ESP32_URL, json=payload_esp, timeout=5)
                    print("📡 Enviado ao ESP32:", resp.text)
                except Exception as e:
                    print("⚠️ Erro ao enviar para ESP32:", e)

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


@app.route("/", methods=["GET"])
def home():
    return "API Flask conectada à maquininha + ESP32", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)