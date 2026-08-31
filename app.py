import os
import time
import re
import platform
import numpy as np
import cv2
import pytesseract
import requests
import unicodedata
from thefuzz import fuzz 
from PIL import Image, ImageOps
from bs4 import BeautifulSoup
import streamlit as st # <--- NOSSO CRIADOR DE TELAS
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning) 

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

headers = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    "Referer": "https://site.cff.org.br/farmaceutico/pesquisa"
}

def remover_acentos(txt):
    return unicodedata.normalize('NFKD', str(txt)).encode('ASCII', 'ignore').decode('ASCII').upper().strip()

def resolver_captcha(caminho_imagem):
    try:
        img_captcha = Image.open(caminho_imagem)
        img_np = np.array(img_captcha.convert("RGB"))
        clean_color = cv2.medianBlur(img_np, 3)
        mask_bg_color = (clean_color[:,:,0] > 180) & (clean_color[:,:,1] > 180) & (clean_color[:,:,2] > 180)
        final_bin = np.zeros_like(clean_color)
        final_bin[mask_bg_color] = [255, 255, 255] 
        final_bin[~mask_bg_color] = [0, 0, 0]      
        
        img_captcha = Image.fromarray(final_bin).convert("L")
        inverted_bg = ImageOps.invert(img_captcha)
        bbox = inverted_bg.getbbox()
        if bbox: img_captcha = img_captcha.crop(bbox)
        
        img_captcha = ImageOps.expand(img_captcha, border=10, fill=255)
        img_np_resized = cv2.resize(np.array(img_captcha.convert("RGB"))[:,:,::-1], None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
        
        config_tess = r'--psm 7 -c tessedit_char_whitelist=0123456789+-'
        texto_limpo = pytesseract.image_to_string(img_np_resized, config=config_tess).strip().replace(" ", "").replace("+-", "+").replace("-+", "-").replace("++", "+").replace("--", "-")
        
        match = re.search(r'(\d+)([+\-])(\d+)', texto_limpo)
        if match:
            num1, operador, num2 = int(match.group(1)), match.group(2), int(match.group(3))
            if num1 <= 50 and num2 <= 50:
                resultado = int(eval(f"{num1}{operador}{num2}"))
                if 0 <= resultado <= 50: return str(resultado)
        return None
    except: return None

def buscar_com_fuzzy(nome_recebido, cidade, estado_sigla):
    nome_alvo_limpo = remover_acentos(nome_recebido)
    primeiro_nome = nome_alvo_limpo.split()[0]
    
    session = requests.Session()
    session.verify = False
    session.headers.update(headers)
    caminho_img_temp = "captcha_fuzzy_temp.png"

    candidatos = []
    pagina_alvo = 1
    last_page_first_hash = None

    try:
        resp = session.get("https://site.cff.org.br/farmaceutico/pesquisa", timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
    except:
        return None, "Falha de conexão com o site do CFF."

    while True:
        tentativa_atual = 0
        sucesso_pagina = False
        soup_resultado = None

        while tentativa_atual < 10:
            try:
                token_tag = soup.find("input", {"name": "_token"})
                if not token_tag: break
                token = token_tag.get("value")
                
                seletor_captcha = "#ImgcaptchaPaginacao img" if pagina_alvo > 1 else "#Imgcaptcha img"
                img_tag = soup.select_one(seletor_captcha) or soup.find("img", src=re.compile(r'captcha', re.I))
                if not img_tag: break
                    
                img_url = img_tag.get('src')
                if not img_url.startswith('http'): img_url = "https://site.cff.org.br" + (img_url if img_url.startswith('/') else '/' + img_url)
                    
                resp_img = session.get(img_url, timeout=15)
                with open(caminho_img_temp, "wb") as f: f.write(resp_img.content)
                
                valor_captcha = resolver_captcha(caminho_img_temp)
                if not valor_captcha:
                    tentativa_atual += 1
                    continue

                payload = {'_token': token, 'uf': estado_sigla, 'cidade': cidade, 'categoria': 'farmaceutico', 'nome': primeiro_nome, 'captcha': valor_captcha, 'search': '1'}
                if pagina_alvo > 1: payload['page'] = str(pagina_alvo)
                
                resp_post = session.post("https://site.cff.org.br/farmaceutico/pesquisar", data=payload, timeout=20)
                if "captcha está incorreto" in resp_post.text.lower() or "favor informar os campos" in resp_post.text.lower():
                    tentativa_atual += 1
                    soup = BeautifulSoup(resp_post.text, 'lxml')
                    continue
                
                soup_resultado = BeautifulSoup(resp_post.text, 'lxml')
                sucesso_pagina = True
                break
            except:
                tentativa_atual += 1
                time.sleep(1)

        if not sucesso_pagina or not soup_resultado: break

        registros = soup_resultado.find_all("div", class_="team-info")
        if not registros: break

        primeiro_registro = registros[0]
        linhas_primeiro = [l.strip() for l in primeiro_registro.text.split("\n") if l.strip()]
        primeiro_nome_hash = linhas_primeiro[0].strip().upper() if len(linhas_primeiro) > 0 else "SEM_NOME"
        primeiro_crf = next((l.replace("CRF", "").replace(":", "").strip() for l in linhas_primeiro if "CRF" in l.upper()), "")
        
        hash_atual = f"{primeiro_nome_hash}|{primeiro_crf}"
        if hash_atual == last_page_first_hash: break 
        last_page_first_hash = hash_atual

        for r in registros:
            linhas = [l.strip() for l in r.text.split("\n") if l.strip()]
            if len(linhas) >= 2:
                raw_nome = linhas[0].strip().upper()
                raw_crf = next((l.replace("CRF", "").replace(":", "").strip() for l in linhas if "CRF" in l.upper()), "")
                candidatos.append({"Nome": raw_nome, "CRF": raw_crf, "Cidade": cidade.upper(), "Estado": estado_sigla.upper()})

        pagina_alvo += 1
        soup = soup_resultado 

    if os.path.exists(caminho_img_temp): os.remove(caminho_img_temp)

    if not candidatos:
        return None, "Nenhum profissional encontrado."

    candidatos_avaliados = []
    for cand in candidatos:
        nota = fuzz.token_set_ratio(nome_alvo_limpo, cand["Nome"])
        cand["Confianca"] = nota
        candidatos_avaliados.append(cand)
        
    candidatos_avaliados.sort(key=lambda x: x["Confianca"], reverse=True)
    return candidatos_avaliados, "Sucesso"


# =======================================================
# INTERFACE GRÁFICA WEB (STREAMLIT)
# =======================================================
st.set_page_config(page_title="Buscador CFF", page_icon="💊")

st.title("💊 Buscador Inteligente CFF")
st.write("Insira os dados do farmacêutico para realizar a busca aproximada.")

# Criando campos de texto na página
col1, col2 = st.columns(2)
with col1:
    estado_input = st.text_input("UF (ex: SP, AM):").upper()
    cidade_input = st.text_input("Cidade (ex: Manaus):").upper()
with col2:
    nome_input = st.text_input("Nome do Farmacêutico:")

# Botão de busca
if st.button("Buscar Profissional"):
    if not estado_input or not cidade_input or not nome_input:
        st.warning("⚠️ Preencha todos os campos antes de buscar.")
    else:
        with st.spinner(f"Quebrando captchas e buscando em {cidade_input}/{estado_input}... Isso pode levar alguns segundos."):
            resultados, msg = buscar_com_fuzzy(nome_input, cidade_input, estado_input)
            
            if resultados:
                st.success("✅ Busca concluída!")
                st.subheader("Melhor Resultado:")
                melhor = resultados[0]
                
                # Exibe o campeão com destaque
                if melhor["Confianca"] >= 70:
                    st.info(f"**Nome:** {melhor['Nome']}\n\n**CRF:** {melhor['CRF']}\n\n**Confiança:** {melhor['Confianca']}%")
                else:
                    st.warning(f"Baixa Confiança ({melhor['Confianca']}%). Verifique os resultados abaixo.")
                
                # Mostra o ranking em uma tabela bonitinha
                st.subheader("Ranking Geral (Outras possibilidades):")
                st.dataframe(resultados)
            else:
                st.error(msg)