from flask import Flask, request, jsonify
import requests
import time
import os

app = Flask(__name__)

# Configurações
NUMERO_SERIE_MAQUINA = "8701372447323147"
ESP32_URL = "http://192.168.5.57/dispense"  # IP do ESP32

# Função simulada de pagamento
def processa_pagamento_maquininha(valor):
    print(f"Enviando pagamento de R$ {valor:.2f} para a máquina {NUMERO_SERIE_MAQUINA}...")
    time.sleep(1)  # simula processamento
    print("Pagamento aprovado pela máquina!")
    return True

# Rota que o catálogo chama
@app.route("/pedido", methods=["POST"])
def criar_pedido():
    data = request.get_json()
    if not data or "items" not in data or "total" not in data:
        return jsonify({"error": "JSON inválido"}), 400

    total = data["total"]
    print(f"Recebido pedido com total R$ {total:.2f} e itens: {data['items']}")

    # Processa pagamento na maquininha
    aprovado = processa_pagamento_maquininha(total)

    if aprovado:
        pedido_para_esp = {
            "order_id": data.get("order_id", f"PED-{int(time.time())}"),
            "total": total,
            "items": data["items"]
        }
        try:
            resp = requests.post(ESP32_URL, json=pedido_para_esp, timeout=5)
            if resp.status_code == 200:
                print("✅ Pedido enviado para o ESP32 com sucesso!")
                return jsonify({"status": "pedido aprovado e enviado para ESP32"}), 200
            else:
                print("❌ Falha ao enviar pedido para ESP32")
                return jsonify({"error": "Falha ao enviar pedido para ESP32"}), 500
        except requests.exceptions.RequestException as e:
            print("❌ Erro na requisição para ESP32:", e)
            return jsonify({"error": str(e)}), 500
    else:
        print("❌ Pagamento não aprovado")
        return jsonify({"status": "pagamento não aprovado"}), 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Porta correta para Heroku
    app.run(host="0.0.0.0", port=port)
