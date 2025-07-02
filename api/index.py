import os
import json
import uuid
import gspread
import resend
from flask import Flask, request, jsonify
from datetime import datetime

# --- CONFIGURAÇÃO (será lida das Variáveis de Ambiente na Vercel) ---
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
GOOGLE_CREDS_JSON = os.environ.get('GOOGLE_CREDS_JSON')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME')
FROM_EMAIL = os.environ.get('FROM_EMAIL') # Ex: "Seu Nome <email@seudominio.com>"

# --- INICIALIZAÇÃO ---
app = Flask(__name__)

# --- FUNÇÕES AUXILIARES ---
def get_google_sheet():
    """Autentica com as credenciais do ambiente e retorna uma instância da planilha."""
    # Carrega as credenciais a partir da variável de ambiente que contém o JSON como string
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    gc = gspread.service_account_from_dict(creds_dict)
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    return spreadsheet.sheet1

# --- ROTA 1: WEBHOOK DA EDUZZ (PARA VENDAS APROVADAS) ---
@app.route('/api/webhook', methods=['POST'])
def eduzz_webhook():
    try:
        data = request.json
        
        # O script agora só processa se for uma Venda Aprovada (status '3')
        if str(data.get('trans_status')) != '3':
            print(f"Webhook recebido com status ignorado: {data.get('trans_status')}")
            return jsonify({'status': 'ignorado'}), 200

        print("Recebido webhook de Venda Aprovada. Processando...")
        sheet = get_google_sheet()
        customer_email = data.get('cus_email')

        if not customer_email:
            raise ValueError("Email do cliente não encontrado no webhook.")
        
        # 1. Gerar Chave de Ativação Única
        license_key = str(uuid.uuid4()).upper()

        # 2. Salvar na Planilha com status inicial 'ATIVA'
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        new_row = [customer_email, license_key, current_time, 'ATIVA']
        sheet.append_row(new_row)
        print(f"Chave ATIVA para {customer_email} foi salva na planilha.")

        # 3. Enviar email para o cliente com a chave
        resend.api_key = RESEND_API_KEY
        params = {
            "from": FROM_EMAIL,
            "to": [customer_email],
            "subject": "Sua Chave de Ativação Chegou!",
            "html": f"<html><body><h2>Olá!</h2><p>Obrigado por sua compra. Aqui está sua chave de ativação:</p><p style='font-size: 20px; font-weight: bold; background-color: #f0f0f0; padding: 10px; border-radius: 5px; text-align: center;'>{license_key}</p><p>Guarde esta chave em um lugar seguro. Você precisará dela e do seu email para ativar o software.</p><p>Atenciosamente,<br>Sua Equipe</p></body></html>",
        }
        resend.Emails.send(params)
        print(f"Email de ativação enviado para {customer_email}.")
        
        return jsonify({'status': 'sucesso'}), 200

    except Exception as e:
        print(f"!!! ERRO GERAL NO PROCESSAMENTO DO WEBHOOK: {e}")
        return jsonify({'status': 'erro', 'detalhes': str(e)}), 500

# --- ROTA 2: VERIFICAÇÃO DE CHAVE (PARA SEU SOFTWARE USAR) ---
@app.route('/api/check_key', methods=['GET'])
def check_key():
    # Pega os parâmetros da URL, ex: /api/check_key?key=SUA_CHAVE&email=SEU_EMAIL
    key = request.args.get('key')
    email = request.args.get('email')

    if not key or not email:
        return jsonify({'status': 'erro', 'message': 'Chave e email são obrigatórios na requisição'}), 400

    try:
        sheet = get_google_sheet()
        
        # Tenta encontrar a célula que contém o email do cliente
        cell = sheet.find(email)
        # Pega todos os valores da linha onde o email foi encontrado
        row_values = sheet.row_values(cell.row)
        
        # Conforme a estrutura da nossa planilha:
        # row_values[0] é o EmailCliente
        # row_values[1] é a ChaveAtivacao
        # row_values[2] é a DataCompra
        # row_values[3] é o Status
        stored_key = row_values[1]
        key_status = row_values[3]

        # Verifica se a chave enviada na URL é a mesma que está na planilha
        # E, o mais importante, se o status daquela linha é 'ATIVA'
        if stored_key.upper() == key.upper() and key_status.upper() == 'ATIVA':
            return jsonify({'status': 'ATIVA', 'message': 'Chave válida e ativa.'})
        else:
            return jsonify({'status': 'INVALIDA', 'message': 'Chave inválida, expirada ou não corresponde ao email.'})
            
    except gspread.exceptions.CellNotFound:
        # Se o `sheet.find(email)` não encontrar nada, ele levanta este erro
        return jsonify({'status': 'INVALIDA', 'message': 'Email não encontrado na base de dados.'})
    except Exception as e:
        print(f"!!! ERRO na verificação de chave: {e}")
        return jsonify({'status': 'erro', 'message': 'Erro interno no servidor de validação.'}), 500