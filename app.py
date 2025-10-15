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
POS_EXTERNAL_ID = "GERTEC_MP35P__8701372447323147"
ESP32_URL = "http://192.168.5.57/liberar"

pedidos_aprovados = []
pedidos_pendentes = {}

# === FUNÇÃO: CRIAR PAGAMENTO NA MAQUININHA ===
def criar_pagamento_maquininha(amount, descricao="Pedido", order_id=None):
    # cancelar qualquer pagamento pendente antes
    limpar_pagamento_maquininha(POS_EXTERNAL_ID)

    url = f"https://api.mercadopago.com/point/integration-api/devices/{POS_EXTERNAL_ID}/payment-intents"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "amount": float(amount),
        "description": descricao,
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response_data = response.json()
        if response.status_code == 201:
            print("✅ Pagamento criado na maquininha!")
            print(json.dumps(response_data, indent=2))
            return response_data
        else:
            print(f"❌ Erro ao criar pagamento: {response.status_code}")
            print(json.dumps(response_data, indent=2))
            return None
    except Exception as e:
        print("Erro ao criar pagamento:", e)
        return None

# === FUNÇÃO: LIMPAR PAGAMENTO PENDENTE NA MAQUININHA ===
def limpar_pagamento_maquininha(serial_number):
    try:
        url = f"https://api.mercadopago.com/point/integration-api/devices/{serial_number}/payment-intents/cancel"
        headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
        r = requests.post(url, headers=headers)
        print(f"🔄 Limpeza da maquininha: {r.status_code}")
    except Exception as e:
        print(f"Erro ao limpar maquininha: {e}")

# === FUNÇÃO: VERIFICAR STATUS DO PAGAMENTO ===
def verificar_pagamento(payment_id):
    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    resp = requests.get(url, headers=headers)
    return resp.json() if resp.ok else None

ID_MAP = {
    1: 15,  # Produto 1 → rele/pino 
    2: 18,
    3: 19,
    4: 21,
    5: 22,
    6: 23,
    7: 13,
    8: 12,
}

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

        descricao = ", ".join([f"{i['name']} x{i['qty']}" for i in itens])
        print(f"🛒 Novo pedido {order_id}: {descricao} | Total R$ {total}")

        # Cria o pagamento na maquininha
        pagamento = criar_pagamento_maquininha(total * 100, descricao, order_id)
        if not pagamento:
            return jsonify({"erro": "Falha ao criar pagamento"}), 500

        # Guarda o pedido pendente incluindo o payment_id corretamente
        pedidos_pendentes[order_id] = {
            "order_id": order_id,
            "itens": itens,
            "total": total,
            "status": "pending",
            "payment_id": pagamento.get("id")  # ✅ Corrigido: dentro do dicionário
        }

        return jsonify({"status": "created", "order_id": order_id}), 200

    except Exception as e:
        print("Erro ao processar pedido:", e)
        return jsonify({"erro": str(e)}), 500

# === ROTA: WEBHOOK DE PAGAMENTO ===
@app.route("/webhook", methods=["POST"])
def webhook():
    info = request.json or {}
    
    # Captura o payment_id corretamente
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
            # Procura o pedido correspondente ao payment_id
            pedido_encontrado = None
            order_ref = None
            for oid, p in pedidos_pendentes.items():
                if p.get("payment_id") == payment_id:
                    pedido_encontrado = p
                    order_ref = oid
                    break

            if pedido_encontrado:
                # Remove do pendente
                pedidos_pendentes.pop(order_ref)
                
                # Limpa pagamento da maquininha
                limpar_pagamento_maquininha(POS_EXTERNAL_ID)

                # === CRIA PAYLOAD PARA ESP32 COM MAPA DE IDS ===
                payload_esp = [
                    {"id": ID_MAP.get(item["id"], 1), "quantidade": item["qty"]}
                    for item in pedido_encontrado["itens"]
                ]

                print("➡️ Payload ESP32 que será enviado:", json.dumps(payload_esp, indent=2))

                # Adiciona pedido aprovado na fila
                pedidos_aprovados.append({
                    "order_id": order_ref,
                    "pedido": payload_esp,
                    "total": pedido_encontrado["total"],
                    "liberado": False
                })

                print(f"✅ Pedido {order_ref} aprovado e enviado para fila do ESP32.")
                print("➡️ Payload ESP32:", payload_esp)

                # ❌ Não tentar notificar ESP32 diretamente se estiver no Heroku
                # ESP32 já vai consultar /esp_pedido periodicamente
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
