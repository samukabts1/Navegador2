#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Upgraded One Browser v5.4 (CEF) - Launcher
===========================================

Este arquivo e o ponto de entrada do navegador.
Como o cefpython3 so funciona no Python 3.9, este launcher detecta a versao
do Python e, se necessario, relanca o navegador usando o ambiente correto
(cefpy39 / Python 3.9).

NAO rode este arquivo com python3.15 ou python3.13. Use sempre:
  - Duplo-clique em: iniciar_cef.bat
  - Ou: "C:\Users\Samuel\miniconda3\envs\cefpy39\python.exe" main.py
"""

import sys
import os
import subprocess


CEF_PY39_PYTHON = r"C:\Users\Samuel\miniconda3\envs\cefpy39\python.exe"
MAIN_CEF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_cef.py")


def _has_cef():
    """Verifica se o Python atual tem o cefpython3 instalado e funcional."""
    try:
        from cefpython3 import cefpython as cef  # noqa: F401
        return True
    except Exception:
        return False


def main():
    # Se o Python atual tem CEF funcional, roda direto o main_cef
    if _has_cef():
        import main_cef
        main_cef.main()
        return

    # Senao, tenta relancar com o Python 3.9 (cefpy39)
    if os.path.exists(CEF_PY39_PYTHON) and os.path.exists(MAIN_CEF):
        print("Detectado Python sem suporte a CEF. Relancando com Python 3.9 (cefpy39)...")
        try:
            subprocess.run([CEF_PY39_PYTHON, MAIN_CEF] + sys.argv[1:], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Erro ao relancar com CEF: {e}")
            sys.exit(e.returncode)
        except Exception as e:
            print(f"Erro inesperado: {e}")
            sys.exit(1)
    else:
        print("ERRO: Nao foi possivel encontrar o ambiente cefpy39 ou o main_cef.py.")
        print("      Certifique-se de que o ambiente cefpy39 esta instalado.")
        print("      Para criar: conda create -n cefpy39 python=3.9 -y")
        print("      E instalar: conda activate cefpy39 && pip install PyQt5 cefpython3")
        sys.exit(1)


if __name__ == "__main__":
    main()
