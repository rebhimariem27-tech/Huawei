"""
backend/llm/fallback_client.py
================================
Client LLM unifié avec fallback automatique multi-fournisseurs.

ORDRE DE FALLBACK (dès qu'un appel échoue — rate limit, timeout, erreur réseau) :
    1. Groq llama-3.3-70b-versatile   (modèle principal, le plus utilisé)
    2. Groq llama-3.1-8b-instant      (quota SÉPARÉ sur Groq — filet de secours immédiat,
                                        aucune nouvelle clé API nécessaire)
    3. Google Gemini 2.5 Flash        (fournisseur différent, tier gratuit généreux)

Si les 3 échouent, lève RateLimitedAllProvidersError — à l'appelant (ArchitectAgent,
DocumentalistAgent, ValidatorAgent) de basculer sur son propre fallback local
(ex: règles heuristiques, déjà en place dans ArchitectAgent).

USAGE :
    from llm.fallback_client import LLMFallbackClient

    llm = LLMFallbackClient()
    resp = llm.complete(
        system_prompt="Tu es l'Agent Architecte...",
        user_prompt=query,
        json_mode=True,
    )
    print(resp.content, resp.provider, resp.model)

INSTALLATION DU FALLBACK GEMINI (optionnel mais recommandé) :
    pip install google-generativeai
    Clé gratuite sur https://aistudio.google.com/apikey
    Ajouter dans .env : GEMINI_API_KEY=...
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from groq import Groq


@dataclass
class LLMResponse:
    content:  str
    provider: str   # "groq" | "gemini"
    model:    str


class RateLimitedAllProvidersError(Exception):
    """Levée quand tous les fournisseurs configurés ont échoué."""


class LLMFallbackClient:

    # Modèle de secours Groq — quota TPD séparé de llama-3.3-70b-versatile
    GROQ_PRIMARY_MODEL  = "openai/gpt-oss-120b"
    GROQ_FALLBACK_MODEL = "openai/gpt-oss-20b"
    GEMINI_MODEL_NAME   = "gemini-2.5-flash"
    REASONING_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}

    def __init__(
        self,
        groq_api_key:   Optional[str] = None,
        gemini_api_key: Optional[str] = None,
    ):
        self._groq_key   = groq_api_key or os.getenv("GROQ_API_KEY")
        self._gemini_key = gemini_api_key or os.getenv("GEMINI_API_KEY")

        self._groq_client = Groq(api_key=self._groq_key) if self._groq_key else None
        if self._groq_client:
            print(f"[LLMFallback] Groq actif — {self.GROQ_PRIMARY_MODEL} "
                  f"(secours: {self.GROQ_FALLBACK_MODEL})")
        else:
            print("[LLMFallback] ⚠ GROQ_API_KEY manquant — Groq désactivé")

        self._gemini_model = None
        if self._gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self._gemini_key)
                self._gemini_model = genai.GenerativeModel(self.GEMINI_MODEL_NAME)
                print(f"[LLMFallback] Gemini actif — {self.GEMINI_MODEL_NAME} (fallback niveau 2)")
            except ImportError:
                print("[LLMFallback] ⚠ google-generativeai non installé — "
                      "'pip install google-generativeai' pour activer le fallback Gemini")
        else:
            print("[LLMFallback] ℹ GEMINI_API_KEY absent — fallback Gemini désactivé "
                  "(optionnel, voir docstring pour l'activer)")

    # ------------------------------------------------------------------
    # API PUBLIQUE
    # ------------------------------------------------------------------

    def complete(
        self,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float = 0.1,
        max_tokens:    int   = 1024,
        json_mode:     bool  = False,
    ) -> LLMResponse:
        """
        Essaie chaque fournisseur/modèle dans l'ordre de fallback.
        Retourne la première réponse réussie.

        Raises:
            RateLimitedAllProvidersError si tous les fournisseurs échouent.
        """
        errors: list[str] = []

        if self._groq_client:
            # 1. Modèle principal
            try:
                return self._call_groq(
                    self.GROQ_PRIMARY_MODEL, system_prompt, user_prompt,
                    temperature, max_tokens, json_mode,
                )
            except Exception as e:
                errors.append(f"Groq {self.GROQ_PRIMARY_MODEL} : {e}")
                print(f"[LLMFallback] ⚠ {errors[-1]}")
                print(f"[LLMFallback] → tentative modèle de secours Groq "
                      f"({self.GROQ_FALLBACK_MODEL})...")

            # 2. Modèle de secours — quota SÉPARÉ sur le même compte Groq
            try:
                return self._call_groq(
                    self.GROQ_FALLBACK_MODEL, system_prompt, user_prompt,
                    temperature, max_tokens, json_mode,
                )
            except Exception as e:
                errors.append(f"Groq {self.GROQ_FALLBACK_MODEL} : {e}")
                print(f"[LLMFallback] ⚠ {errors[-1]}")
                if self._gemini_model:
                    print("[LLMFallback] → tentative Gemini (fournisseur différent)...")

        # 3. Fournisseur différent — Gemini
        if self._gemini_model:
            try:
                return self._call_gemini(system_prompt, user_prompt, temperature, max_tokens)
            except Exception as e:
                errors.append(f"Gemini {self.GEMINI_MODEL_NAME} : {e}")
                print(f"[LLMFallback] ⚠ {errors[-1]}")

        raise RateLimitedAllProvidersError(
            "Tous les fournisseurs LLM ont échoué :\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # ------------------------------------------------------------------
    # APPELS PAR FOURNISSEUR
    # ------------------------------------------------------------------

    def _call_groq(
        self,
        model: str,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float,
        max_tokens:    int,
        json_mode:     bool,
    ) -> LLMResponse:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
         # Modèles reasoning : on force un effort de raisonnement minimal et
        # un format qui exclut le raisonnement du texte final, pour ne pas
        # gaspiller max_tokens et ne pas polluer le JSON attendu.
        if model in self.REASONING_MODELS:
            kwargs["reasoning_effort"] = "low"
            kwargs["reasoning_format"] = "hidden"   # exclut le raisonnement de .content


        resp = self._groq_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return LLMResponse(
            content=resp.choices[0].message.content,
            provider="groq",
            model=model,
        )

    def _call_gemini(
        self,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float,
        max_tokens:    int,
    ) -> LLMResponse:
        # Gemini n'a pas de rôle "system" séparé dans generate_content simple —
        # on le préfixe au prompt utilisateur.
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        resp = self._gemini_model.generate_content(
            full_prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
        )
        return LLMResponse(
            content=resp.text,
            provider="gemini",
            model=self.GEMINI_MODEL_NAME,
        )