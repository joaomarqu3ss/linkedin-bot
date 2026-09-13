import time
import sys
import urllib.parse
import os
import platform

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from webdriver_manager.core.os_manager import ChromeType
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def get_profile_path(browser: str) -> str:
    system = platform.system()
    paths = {
        "Darwin": {
            "chrome": "~/Library/Application Support/Google/Chrome",
            "brave": "~/Library/Application Support/BraveSoftware/Brave-Browser",
            "edge": "~/Library/Application Support/Microsoft Edge",
            "vivaldi": "~/Library/Application Support/Vivaldi"
        },
        "Windows": {
            "chrome": "~\\AppData\\Local\\Google\\Chrome\\User Data",
            "brave": "~\\AppData\\Local\\BraveSoftware\\Brave-Browser\\User Data",
            "edge": "~\\AppData\\Local\\Microsoft\\Edge\\User Data",
            "vivaldi": "~\\AppData\\Local\\Vivaldi\\User Data"
        },
        "Linux": {
            "chrome": "~/.config/google-chrome",
            "brave": "~/.config/BraveSoftware/Brave-Browser",
            "edge": "~/.config/microsoft-edge",
            "vivaldi": "~/.config/vivaldi"
        }
    }
    
    # Fallback to macOS paths if system is not found
    os_paths = paths.get(system, paths["Darwin"])
    profile_path = os_paths.get(browser, os_paths["chrome"])
    return os.path.expanduser(profile_path)

def run_automation(config: dict):
    browser_choice = config.get("browser", "chrome")
    print(f"Iniciando automação do Selenium (Navegador: {browser_choice.capitalize()})...")
    print(f"Buscando vagas de Candidatura Simplificada para: {config.get('first_name')} {config.get('last_name')}")
    
    profile_path = get_profile_path(browser_choice)
    
    if browser_choice == "edge":
        options = webdriver.EdgeOptions()
        options.add_argument(f"user-data-dir={profile_path}")
        options.add_argument("profile-directory=Default")
        service = EdgeService(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=options)
        
    else:
        options = webdriver.ChromeOptions()
        options.add_argument(f"user-data-dir={profile_path}")
        options.add_argument("profile-directory=Default")
        
        if browser_choice == "brave":
            service = ChromeService(ChromeDriverManager(chrome_type=ChromeType.BRAVE).install())
            # Path comum do Brave no Mac (se não encontrar o selenium tentará o default)
            if platform.system() == "Darwin":
                options.binary_location = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        elif browser_choice == "vivaldi":
            service = ChromeService(ChromeDriverManager().install())
            if platform.system() == "Darwin":
                options.binary_location = "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"
        else:
            service = ChromeService(ChromeDriverManager().install())
            
        driver = webdriver.Chrome(service=service, options=options)
    
    try:
        # Acessa o LinkedIn Jobs direto com o filtro de Candidatura Simplificada e Localidade (Brasil)
        jobs_url = "https://www.linkedin.com/jobs/search/?f_AL=true&position=1&pageNum=0&geoId=108047850"
        
        keywords = config.get("keywords")
        if keywords:
            encoded_keywords = urllib.parse.quote(keywords)
            jobs_url += f"&keywords={encoded_keywords}"
            
        print(f"Navegando para: {jobs_url}")
        driver.get(jobs_url)
        
        # Pausa para o usuário acompanhar (COMENTADO TEMPORARIAMENTE)
        # input(f"\n[PAUSA] O navegador deve ter aberto já logado com seu perfil do {browser_choice.capitalize()}.\nPressione ENTER aqui no terminal para iniciar a tentativa de candidatura...")
        
        # Aguarda a página carregar (espera o body estar presente)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        print("Página principal carregada. Aguardando renderização do React...")
        
        # Checa se o MacOS bloqueou o acesso aos cookies (comum no Keychain)
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li.jobs-search-results__list-item, div.job-card-container, .jobs-search-results-list"))
            )
        except:
            print("\n[ATENÇÃO] O LinkedIn pediu login! (O Keychain do Mac às vezes bloqueia os cookies do Brave para o robô).")
            print("⏳ VOCÊ TEM 45 SEGUNDOS para logar rapidamente na janela do Brave que abriu!")
            time.sleep(45)
            
        time.sleep(5) # Aguarda 5 segundos adicionais para garantir a interface
        
        # 1. Clicar no primeiro job card para forçar o carregamento do painel direito
        try:
            print("Procurando a lista de vagas para clicar na primeira opção...")
            first_job = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "li.jobs-search-results__list-item, div.job-card-container, div[data-job-id]"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", first_job)
            time.sleep(1)
            first_job.click()
            print("Primeira vaga clicada. Aguardando o painel de detalhes abrir...")
            time.sleep(4)
        except Exception as e:
            print(f"Aviso: Não encontrou card de vaga para clicar, seguindo em frente. Erro: {e}")
            
        # 2. Clicar no botão 'Candidatura simplificada'
        try:
            print("Procurando o botão de Candidatura simplificada...")
            # Busca ESTRITAMENTE botões que contenham o texto de candidatura simplificada (ignora botões de vagas externas)
            apply_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Candidatura simplificada') or contains(., 'Easy Apply')]"))
            )
            
            # Garante que a tela foque no botão antes de clicar
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", apply_button)
            time.sleep(1)
            apply_button.click()
            print("Botão clicado! Modal aberto.")
        except Exception as e:
            print("Não foi possível encontrar o botão de Candidatura Simplificada na vaga.", file=sys.stderr)
            return

        time.sleep(3)
        
        # 2. Loop para navegar pelas etapas do modal
        cv_path = config.get('cv_file', '')
        phone = config.get('phone_number', '')
        
        for step in range(10): # Limite de passos
            print(f"--- Processando etapa {step+1} do formulário ---")
            time.sleep(2) # Espera a animação
            
            # Verifica se há mensagens de erro (formulário travado por perguntas obrigatórias)
            error_msgs = driver.find_elements(By.CSS_SELECTOR, ".artdeco-inline-feedback--error, [data-test-form-builder-error-message]")
            if error_msgs and len(error_msgs) > 0:
                print("⚠️ Formulário travado! Há perguntas obrigatórias do recrutador que precisam de intervenção humana.")
                print("Interrompendo automação para não cometer erros nas respostas.")
                break
                
            # Tentar preencher o currículo
            try:
                file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
                for file_input in file_inputs:
                    if cv_path and os.path.exists(cv_path):
                        file_input.send_keys(cv_path)
                        print(f"Currículo anexado: {cv_path}")
            except:
                pass
                
            # Tentar preencher número de telefone
            try:
                text_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
                for t_input in text_inputs:
                    label = t_input.find_element(By.XPATH, "./ancestor::div[1]//label").text.lower()
                    if "telefone" in label or "phone" in label:
                        t_input.clear()
                        t_input.send_keys(phone)
                        print(f"Telefone preenchido: {phone}")
            except:
                pass
            
            # Buscar botão principal de ação da tela
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                action_btn = None
                
                for btn in all_buttons:
                    try:
                        text = btn.text.lower()
                        if any(word in text for word in ["avançar", "next", "revisar", "review", "avaliar", "enviar", "submit"]):
                            action_btn = btn
                            break
                    except:
                        continue
                        
                if action_btn:
                    btn_text = action_btn.text.lower()
                    if "enviar" in btn_text or "submit" in btn_text:
                        print("Encontrado botão de enviar! Submetendo candidatura final...")
                        action_btn.click() # CLICA PARA ENVIAR REALMENTE
                        print("Fim do fluxo atingido com sucesso. Candidatura enviada!")
                        break
                    else:
                        print(f"Clicando no botão: {action_btn.text}")
                        action_btn.click()
                else:
                    print("Nenhum botão de ação (Avançar/Enviar) encontrado. Pode ser o fim do formulário ou um erro.")
                    break
            except Exception as e:
                print(f"Erro ao interagir com o botão: {e}")
                break
                
        time.sleep(5)
        
    except Exception as e:
        print(f"Erro na automação: {e}", file=sys.stderr)
    finally:
        print("Fechando o navegador.")
        driver.quit()
