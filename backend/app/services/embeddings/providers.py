from abc import ABC, abstractmethod
import math
import httpx

class ProviderError(RuntimeError):
    pass

class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings):
        self.settings = settings
    def embed(self, texts):
        try:
            # The original is stored separately; Ollama may bound only its vector input.
            response = httpx.post(self.settings.ollama_url + "/api/embed", json={"model":self.settings.embedding_model,"input":texts,"truncate":True,"keep_alive":"5m"}, timeout=self.settings.model_timeout)
            response.raise_for_status()
            vectors = response.json()["embeddings"]
            if len(vectors) != len(texts) or not vectors:
                raise ValueError("invalid batch")
            dimension = len(vectors[0])
            if not dimension or any(len(v) != dimension or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors):
                raise ValueError("invalid vectors")
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Modèle d'embeddings indisponible ou réponse invalide") from exc

def embedding_provider(settings):
    return OllamaEmbeddingProvider(settings) if settings.embedding_provider == "ollama" else None
