from flask import Flask, request, jsonify
from threading import Timer
import requests
import os

app = Flask(__name__)

# Lista de pedidos pendentes
pedidos = []

# === CONFIGURAÇÃO MERCADO PAGO ===
ACCESS_TOKEN = 'APP_USR-3319944673883642-101218-671fe3f886fe928cac84e01bc31bc20a-1433246274'  # Substitua pelo seu token do Mercado Pago

def criar_pagamento_mercadopago(valor, descricao):
    """
    Cria um pagamento via API do Mercado Pago (Pix ou cartão)
    """
    url = 'https://api.mercadopago.com/v1/payments'
    headers = {
        'Authorization': f'Bearer {ACCESS_TOKEN}',
        'Content-Type': 'application/json'
    }
    data = {
        "transaction_amount": float(valor),
        "description": descricao,
        "payment_method_id": "pix",  # Pode trocar para "credit_card" se quiser cartão
        "payer": {
            "email": "cliente@exemplo.com"  # Pode ser qualquer email
        }
    }
    r = requests.post(url, json=data, headers=headers)
    return r.json()

# Função para remover pedido após 10 segundos
def remover_pedido(pedido_id):
    global pedidos
    pedidos = [p for p in pedidos if p['order_id'] != pedido_id]

# Rota para receber pedido do ESP32
@app.route('/pedido', methods=['POST'])
def receber_pedido():
    global pedidos
    pedido = request.json
    if not pedido:
        return jsonify({"error": "JSON inválido"}), 400

    pedido['status'] = 'pending'
    pedidos.append(pedido)

    # Timer para remover pedido após 10 segundos
    t = Timer(10, remover_pedido, args=[pedido['order_id']])
    t.start()

    return jsonify({"status": "pedido recebido"}), 200

# Rota para ESP32 consultar pedido pendente
@app.route('/getPedido', methods=['GET'])
def get_pedido():
    global pedidos
    for pedido in pedidos:
        if pedido['status'] == 'pending':
            pedido['status'] = 'read'
            return jsonify(pedido), 200
    # Retornar um JSON vazio com status 200
    return jsonify({"status": "none"}), 200

# Rota para gerar pagamento direto (Pix ou cartão) via Mercado Pago
@app.route('/gerarPagamento', methods=['POST'])
def gerar_pagamento():
    data = request.json
    valor = data.get('valor')
    descricao = data.get('descricao', 'Pedido ESP32')

    if not valor:
        return jsonify({"error": "Valor é obrigatório"}), 400

    pagamento = criar_pagamento_mercadopago(valor, descricao)
    return jsonify(pagamento), 200

# Rota de teste
@app.route('/', methods=['GET'])
def home():
    return "Servidor Flask online e pronto!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)