import os
import json
import uuid
import gspread
import resend
from flask import Flask, request, jsonify
from datetime import datetime

# --- CONFIGURAÇÃO (lida das Variáveis de Ambiente na Vercel) ---
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
GOOGLE_CREDS_JSON = os.environ.get('GOOGLE_CREDS_JSON')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME')
FROM_EMAIL = os.environ.get('FROM_EMAIL') 

# --- INICIALIZAÇÃO ---
app = Flask(__name__)

# --- FUNÇÕES AUXILIARES ---
def get_google_sheet():
    """Autentica e retorna uma instância da planilha."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    gc = gspread.service_account_from_dict(creds_dict)
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    return spreadsheet.sheet1

# --- ROTA 1: WEBHOOK DA EDUZZ (AGORA ACEITA GET PARA VALIDAÇÃO) ---
@app.route('/api/webhook', methods=['GET', 'POST'])
def eduzz_webhook():
    # --- INÍCIO DA MUDANÇA ---
    # Se a requisição for do tipo GET (do validador da Eduzz),
    # apenas retorna uma mensagem de sucesso para ele.
    if request.method == 'GET':
        print("Recebida requisição GET de validação da Eduzz. Respondendo com sucesso.")
        return jsonify({'status': 'validação bem-sucedida'}), 200
    # --- FIM DA MUDANÇA ---

    # Se a requisição for POST, o código continua normalmente...
    if request.method == 'POST':
        try:
            data = request.json
            
            if str(data.get('trans_status')) != '3':
                print(f"Webhook POST recebido com status ignorado: {data.get('trans_status')}")
                return jsonify({'status': 'ignorado'}), 200

            print("Recebido webhook de Venda Aprovada. Processando...")
            sheet = get_google_sheet()
            customer_email = data.get('cus_email')

            if not customer_email:
                raise ValueError("Email do cliente não encontrado no webhook.")
            
            license_key = str(uuid.uuid4()).upper()
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            new_row = [customer_email, license_key, current_time, 'ATIVA']
            sheet.append_row(new_row)
            print(f"Chave ATIVA para {customer_email} foi salva na planilha.")

            resend.api_key = RESEND_API_KEY
            params = {
                "from": FROM_EMAIL,
                "to": [customer_email],
                "subject": "Sua Chave de Ativação Chegou!",
                "html": f"<html><body><h2>Olá!</h2><p>Obrigado por sua compra. Aqui está sua chave de ativação:</p><p style='font-size: 20px; font-weight: bold; background-color: #f0f0f0; padding: 10px; border-radius: 5px; text-align: center;'>{license_key}</p><p>Guarde esta chave em um lugar seguro.</p><p>Atenciosamente,<br>Sua Equipe</p></body></html>",
            }
            resend.Emails.send(params)
            print(f"Email de ativação enviado para {customer_email}.")
            
            return jsonify({'status': 'sucesso'}), 200

        except Exception as e:
            print(f"!!! ERRO GERAL NO PROCESSAMENTO DO WEBHOOK: {e}")
            return jsonify({'status': 'erro', 'detalhes': str(e)}), 500

# Rota de verificação de chave continua a mesma...
@app.route('/api/check_key', methods=['GET'])
def check_key():
    key = request.args.get('key')
    email = request.args.get('email')

    if not key or not email:
        return jsonify({'status': 'erro', 'message': 'Chave e email são obrigatórios'}), 400

    try:
        sheet = get_google_sheet()
        cell = sheet.find(email)
        row_values = sheet.row_values(cell.row)
        
        stored_key = row_values[1]
        key_status = row_values[3]

        if stored_key.upper() == key.upper() and key_status.upper() == 'ATIVA':
            return jsonify({'status': 'ATIVA', 'message': 'Chave válida e ativa.'})
        else:
            return jsonify({'status': 'INVALIDA', 'message': 'Chave inválida, expirada ou não corresponde.'})
            
    except gspread.exceptions.CellNotFound:
        return jsonify({'status': 'INVALIDA', 'message': 'Email não encontrado.'})
    except Exception as e:
        print(f"!!! ERRO na verificação de chave: {e}")
        return jsonify({'status': 'erro', 'message': 'Erro interno no servidor.'}), 500
