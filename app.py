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

# === Criar pasta local para armazenar pedidos aprovados ===
PASTA_PEDIDOS = "pedidos_aprovados"
os.makedirs(PASTA_PEDIDOS, exist_ok=True)

# === FUNÇÃO: CRIAR PAGAMENTO NA MAQUININHA ===
def criar_pagamento_maquininha(amount, descricao="Pedido", order_id=None):
    limpar_pagamento_maquininha(POS_EXTERNAL_ID)
    time.sleep(1.5)

    url = f"https://api.mercadopago.com/point/integration-api/devices/{POS_EXTERNAL_ID}/payment-intents"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json"}

    payload = {
        "amount": int(float(amount) * 100),
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
    order_ref = "PED-" + str(int(time.time() * 1000))

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
            pedido_encontrado = None
            order_ref = None
            for oid, p in pedidos_pendentes.items():
                if p.get("payment_id") == payment_id:
                    pedido_encontrado = p
                    order_ref = oid
                    break

            if pedido_encontrado:
                pedidos_pendentes.pop(order_ref, None)

                # Cancela qualquer cobrança pendente na maquininha
                print("🧹 Limpando cobranças anteriores na maquininha...")
                try:
                    limpar_pagamento_maquininha(POS_EXTERNAL_ID)
                    time.sleep(1)
                    limpar_pagamento_maquininha(POS_EXTERNAL_ID)
                except Exception as e:
                    print("⚠️ Falha ao limpar maquininha após aprovação:", e)

                payload_esp = [{"id": item["id"], "quantidade": item["qty"]} for item in pedido_encontrado["itens"]]

                pedido_salvar = {
                    "order_id": order_ref,
                    "pedido": payload_esp,
                    "total": pedido_encontrado["total"],
                    "liberado": False
                }

                # ✅ Salvar o pedido aprovado em arquivo JSON
                arquivo_pedido = os.path.join(PASTA_PEDIDOS, f"pedido_{order_ref}.json")
                with open(arquivo_pedido, "w", encoding="utf-8") as f:
                    json.dump(pedido_salvar, f, ensure_ascii=False, indent=2)

                print(f"💾 Pedido salvo em: {arquivo_pedido}")

                # Notifica o ESP32
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

    # Verifica arquivos na pasta
    arquivos = sorted(os.listdir(PASTA_PEDIDOS))
    if arquivos:
        arquivo = os.path.join(PASTA_PEDIDOS, arquivos[0])
        with open(arquivo, "r", encoding="utf-8") as f:
            pedido = json.load(f)
        os.remove(arquivo)  # ✅ remove após enviar
        print("➡️ Enviando pedido do arquivo:", arquivo)
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
