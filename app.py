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
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json"}

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

# === ROTA: RECEBER PEDIDO DO CATÁLOGO ===
@app.route("/pedido", methods=["POST"])
def criar_pedido():
    data = request.json or {}
    itens = data.get("items", [])
    total = data.get("total", 0.0)
    
    if not itens:
        return jsonify({"status": "erro", "mensagem": "Nenhum item enviado"}), 400

    # Gera um ID único para o pedido
    order_ref = "PED-" + str(int(time.time()*1000))

    # Cria descrição para a maquininha
    descricao = ", ".join([f"{i['name']} x{i['qty']}" for i in itens])

    # === Cria pagamento na maquininha ===
    try:
        pagamento = criar_pagamento_maquininha(total, descricao, POS_EXTERNAL_ID)
        payment_id = pagamento.get("id")
        print(f"💳 Pagamento criado na maquininha! Payment ID: {payment_id}")
    except Exception as e:
        print("⚠️ Erro ao criar pagamento na maquininha:", e)
        return jsonify({"status": "erro", "mensagem": "Falha ao criar pagamento"}), 500

    # === Salva o pedido nos pendentes, incluindo payment_id ===
    pedidos_pendentes[order_ref] = {
        "itens": itens,
        "total": total,
        "payment_id": payment_id
    }

    print(f"📝 Pedido salvo em pendentes: {order_ref}")
    print(json.dumps(pedidos_pendentes[order_ref], indent=2))

    # Retorna info básica para o frontend
    return jsonify({
        "status": "ok",
        "order_id": order_ref,
        "payment_id": payment_id,
        "total": total
    }), 200


# === ROTA: WEBHOOK - RECEBIMENTO DE STATUS DE PAGAMENTO ===
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
            # Procurar o pedido correspondente ao payment_id
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
                limpar_pagamento_maquininha(POS_EXTERNAL_ID)

                # Cria payload correto para ESP
                payload_esp = [{"id": item["id"], "quantidade": item["qty"]} for item in pedido_encontrado["itens"]]

                pedidos_aprovados.append({
                    "order_id": order_ref,
                    "pedido": payload_esp,
                    "total": pedido_encontrado["total"],
                    "liberado": False
                })

                print(f"✅ Pedido {order_ref} aprovado e enviado para fila do ESP32.")
                try:
                    r = requests.get(ESP32_URL, timeout=5)
                    print("📡 ESP32 notificado:", r.status_code)
                except Exception as e:
                    print("⚠️ Falha ao notificar ESP32:", e)
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
