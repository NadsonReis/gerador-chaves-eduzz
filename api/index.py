import os
import json
import uuid
import gspread
import resend
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta

# --- CONFIGURAÇÃO (lida das Variáveis de Ambiente na Vercel) ---
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
GOOGLE_CREDS_JSON = os.environ.get('GOOGLE_CREDS_JSON')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME')
FROM_EMAIL = os.environ.get('FROM_EMAIL')

# Opcional: protege o webhook. Se definido, a URL cadastrada na Eduzz deve terminar com ?token=<valor>
WEBHOOK_TOKEN = os.environ.get('WEBHOOK_TOKEN')

# Versão 1.0 do MoveBot não envia o ID do computador.
# '1' (padrão) = aceita esses pedidos sem vincular computador (período de transição).
# '0' = recusa e pede para instalar a versão nova (use depois do lançamento da 2.0).
PERMITIR_VERSAO_ANTIGA = os.environ.get('PERMITIR_VERSAO_ANTIGA', '1') == '1'

# --- COLUNAS DA PLANILHA ---
# A: Email | B: Chave | C: Data da compra | D: Status | E: Computador | F: Ativado em | G: Última verificação | H: Versão do app
COL_EMAIL, COL_CHAVE, COL_DATA, COL_STATUS, COL_COMPUTADOR, COL_ATIVADO_EM, COL_ULTIMA_VERIF, COL_VERSAO = range(8)
TOTAL_COLUNAS = 8

FUSO_BR = timezone(timedelta(hours=-3))

# --- INICIALIZAÇÃO ---
app = Flask(__name__)


# --- FUNÇÕES AUXILIARES ---
def get_google_sheet():
    """Autentica e retorna uma instância da planilha."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    gc = gspread.service_account_from_dict(creds_dict)
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    return spreadsheet.sheet1


def agora_br():
    return datetime.now(FUSO_BR).strftime('%Y-%m-%d %H:%M:%S')


def celula(linha, indice):
    """Valor de uma coluna da linha (linhas antigas podem ter menos colunas)."""
    return linha[indice].strip() if len(linha) > indice and linha[indice] else ''


def gravar_colunas(sheet, numero_linha, col_inicial, valores):
    """Grava valores a partir de uma coluna (0 = A) numa única chamada à API."""
    if sheet.col_count < TOTAL_COLUNAS:
        sheet.add_cols(TOTAL_COLUNAS - sheet.col_count)
    letra_ini = chr(ord('A') + col_inicial)
    letra_fim = chr(ord('A') + col_inicial + len(valores) - 1)
    sheet.batch_update([{
        'range': f'{letra_ini}{numero_linha}:{letra_fim}{numero_linha}',
        'values': [valores]
    }])


# --- ROTA 1: WEBHOOK DA EDUZZ ---
@app.route('/api/webhook', methods=['GET', 'POST'])
def eduzz_webhook():
    # Validação da Eduzz (GET)
    if request.method == 'GET':
        print("Recebida requisição GET de validação da Eduzz. Respondendo com sucesso.")
        return jsonify({'status': 'validação bem-sucedida'}), 200

    # Proteção opcional contra vendas falsas
    if WEBHOOK_TOKEN and request.args.get('token') != WEBHOOK_TOKEN:
        print("Webhook recusado: token ausente ou incorreto.")
        return jsonify({'status': 'nao autorizado'}), 403

    try:
        data = request.json or request.form.to_dict()

        if str(data.get('trans_status')) != '3':
            print(f"Webhook POST recebido com status ignorado: {data.get('trans_status')}")
            return jsonify({'status': 'ignorado'}), 200

        print("Recebido webhook de Venda Aprovada. Processando...")
        sheet = get_google_sheet()
        customer_email = (data.get('cus_email') or '').strip()

        if not customer_email:
            raise ValueError("Email do cliente não encontrado no webhook.")

        license_key = str(uuid.uuid4()).upper()
        # Colunas E-H ficam vazias: o computador é vinculado na primeira ativação
        new_row = [customer_email, license_key, agora_br(), 'ATIVA']
        sheet.append_row(new_row)
        print(f"Chave ATIVA para {customer_email} foi salva na planilha.")

        resend.api_key = RESEND_API_KEY
        params = {
            "from": FROM_EMAIL,
            "to": [customer_email],
            "subject": "Sua Chave de Ativação Chegou!",
            "html": f"<html><body><h2>Olá!</h2><p>Obrigado por sua compra. Aqui está sua chave de ativação:</p><p style='font-size: 20px; font-weight: bold; background-color: #f0f0f0; padding: 10px; border-radius: 5px; text-align: center;'>{license_key}</p><p>Esta chave pode ser ativada em <strong>1 (um) computador</strong>. Guarde-a em um lugar seguro.</p><p>Atenciosamente,<br>Sua Equipe</p></body></html>",
        }
        resend.Emails.send(params)
        print(f"Email de ativação enviado para {customer_email}.")

        return jsonify({'status': 'sucesso'}), 200

    except Exception as e:
        print(f"!!! ERRO GERAL NO PROCESSAMENTO DO WEBHOOK: {e}")
        return jsonify({'status': 'erro', 'detalhes': str(e)}), 500


# --- ROTA 2: VERIFICAÇÃO DA CHAVE (1 chave = 1 computador) ---
@app.route('/api/check_key', methods=['GET'])
def check_key():
    key = (request.args.get('key') or '').strip()
    email = (request.args.get('email') or '').strip()
    machine = (request.args.get('machine') or '').strip()
    version = (request.args.get('version') or '').strip()

    if not key or not email:
        return jsonify({'status': 'INVALIDA', 'message': 'Chave e email são obrigatórios.'}), 400

    try:
        sheet = get_google_sheet()
        linhas = sheet.get_all_values()

        # Procura a linha com ESTE e-mail E ESTA chave (o mesmo e-mail pode ter comprado mais de uma vez)
        encontrada = None
        for i, linha in enumerate(linhas):
            if celula(linha, COL_EMAIL).lower() == email.lower() and celula(linha, COL_CHAVE).upper() == key.upper():
                encontrada = (i + 1, linha)   # número da linha na planilha (começa em 1)
                break

        if not encontrada:
            return jsonify({'status': 'INVALIDA', 'message': 'Chave inválida, expirada ou não corresponde.'})

        numero_linha, linha = encontrada

        if celula(linha, COL_STATUS).upper() != 'ATIVA':
            return jsonify({'status': 'INVALIDA', 'message': 'Esta licença não está ativa. Fale com o suporte.'})

        # Pedido da versão 1.0 (sem ID do computador)
        if not machine:
            if PERMITIR_VERSAO_ANTIGA:
                return jsonify({'status': 'ATIVA', 'message': 'Chave válida e ativa.'})
            return jsonify({'status': 'ATUALIZAR', 'message': 'Instale a versão mais recente do MoveBot para ativar sua licença.'})

        computador_vinculado = celula(linha, COL_COMPUTADOR)

        # Primeira ativação (inclui todos os clientes antigos): vincula a este computador
        if not computador_vinculado:
            agora = agora_br()
            gravar_colunas(sheet, numero_linha, COL_COMPUTADOR, [machine, agora, agora, version])
            print(f"Licença de {email} vinculada ao computador {machine}.")
            return jsonify({'status': 'ATIVA', 'message': 'Licença ativada neste computador.'})

        # Mesmo computador: tudo certo
        if computador_vinculado == machine:
            gravar_colunas(sheet, numero_linha, COL_ULTIMA_VERIF, [agora_br(), version])
            return jsonify({'status': 'ATIVA', 'message': 'Chave válida e ativa.'})

        # Outro computador
        print(f"Tentativa de ativar a licença de {email} em outro computador ({machine}).")
        return jsonify({
            'status': 'EM_USO',
            'message': 'Esta chave já está ativada em outro computador. Para transferir a licença, fale com o suporte.'
        })

    except Exception as e:
        print(f"!!! ERRO na verificação de chave: {e}")
        # 'erro' = resposta inconclusiva: o app NÃO desativa o cliente por causa disso
        return jsonify({'status': 'erro', 'message': 'Erro interno no servidor. Tente novamente.'}), 500
