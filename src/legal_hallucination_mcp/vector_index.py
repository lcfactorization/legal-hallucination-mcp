"""向量索引模块 — 将法律文本矢量化，构建适合LLM检索的索引。

桥接架构：不调用任何LLM API。使用轻量级本地Embedding模型（如bge-large-zh-v1.5）
或纯文本分块+关键词索引的方式，将法律法规、案例、学术参考等文本矢量化，
构建适合LLM检索的向量索引，提高检索效率和准确性。

当本地Embedding模型不可用时，自动降级为纯关键词索引模式。
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime

from pydantic import BaseModel, Field

logger = logging.getLogger("legal-hallucination")

EMBEDDING_AVAILABLE = False
_embedding_model = None

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
    logger.info("向量索引模块：sentence-transformers 可用，将使用本地Embedding模型")
except ImportError:
    logger.info("向量索引模块：sentence-transformers 不可用，将使用纯关键词索引模式")


class VectorDocument(BaseModel):
    doc_id: str = Field(default="", description="文档唯一标识")
    doc_type: str = Field(default="", description="文档类型: 法条/案例/学术/原则/证据")
    title: str = Field(default="", description="文档标题")
    content: str = Field(default="", description="文档内容")
    metadata: dict = Field(default_factory=dict, description="元数据")
    chunk_index: int = Field(default=0, description="分块序号")
    chunk_text: str = Field(default="", description="分块文本")
    keywords: list[str] = Field(default_factory=list, description="关键词列表")
    embedding_hash: str = Field(default="", description="嵌入向量哈希（用于缓存验证）")
    source_file: str = Field(default="", description="来源文件路径")
    created_at: str = Field(default="", description="创建时间")


class SearchResult(BaseModel):
    doc_id: str = Field(default="")
    doc_type: str = Field(default="")
    title: str = Field(default="")
    chunk_text: str = Field(default="")
    score: float = Field(default=0.0, description="相似度分数 0-1")
    keywords_matched: list[str] = Field(default_factory=list, description="匹配的关键词")
    metadata: dict = Field(default_factory=dict)


class VectorIndexStats(BaseModel):
    total_documents: int = Field(default=0)
    total_chunks: int = Field(default=0)
    doc_types: dict[str, int] = Field(default_factory=dict)
    embedding_mode: str = Field(default="关键词索引")
    index_size_mb: float = Field(default=0.0)
    last_updated: str = Field(default="")


CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
EMBEDDING_MODEL_NAME = "BAAI/bge-large-zh-v1.5"
EMBEDDING_DIMENSION = 1024


def _extract_keywords(text: str, max_keywords: int = 20) -> list[str]:
    legal_stopwords = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有",
        "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些", "么",
        "什么", "如何", "怎么", "哪", "哪些", "为什么", "因为", "所以", "但是",
        "如果", "虽然", "而且", "或者", "以及", "及其", "等", "等等", "之",
        "其", "该", "此", "本", "各", "每", "某", "任", "凡", "所有",
        "应当", "可以", "不得", "必须", "需要", "按照", "依照", "根据",
        "关于", "对于", "由于", "基于", "鉴于", "为了", "以",
    }

    chinese_words = re.findall(r'[\u4e00-\u9fff]{2,8}', text)

    freq = {}
    for word in chinese_words:
        if word not in legal_stopwords and len(word) >= 2:
            freq[word] = freq.get(word, 0) + 1

    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:max_keywords]]


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            boundary = text.rfind('\n', start + chunk_size - overlap, end)
            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap
        if start >= len(text):
            break

    return chunks


def _compute_content_hash(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()[:16]


class VectorIndex:
    def __init__(self, index_dir: str = "", use_embedding: bool = True):
        self.index_dir = index_dir
        self.documents: list[VectorDocument] = []
        self.keyword_index: dict[str, list[str]] = {}
        self.doc_type_index: dict[str, list[str]] = {}
        self.embedding_cache: dict[str, list[float]] = {}
        self.use_embedding = use_embedding and EMBEDDING_AVAILABLE
        self._embedding_model = None
        self.loaded = False

    def _get_embedding_model(self):
        if self._embedding_model is not None:
            return self._embedding_model

        if not EMBEDDING_AVAILABLE:
            logger.warning("向量索引：Embedding模型不可用，使用关键词索引模式")
            return None

        try:
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("向量索引：已加载Embedding模型 %s", EMBEDDING_MODEL_NAME)
            return self._embedding_model
        except Exception as e:
            logger.warning("向量索引：加载Embedding模型失败: %s，使用关键词索引模式", e)
            self.use_embedding = False
            return None

    def index_document(
        self,
        doc_type: str,
        title: str,
        content: str,
        metadata: dict | None = None,
        source_file: str = "",
    ) -> list[str]:
        doc_ids = []
        chunks = _chunk_text(content)
        content_hash = _compute_content_hash(content)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for idx, chunk in enumerate(chunks):
            chunk_keywords = _extract_keywords(chunk, max_keywords=10)

            doc_id = f"{doc_type}_{content_hash}_{idx}"

            doc = VectorDocument(
                doc_id=doc_id,
                doc_type=doc_type,
                title=title,
                content=content[:500],
                metadata=metadata or {},
                chunk_index=idx,
                chunk_text=chunk,
                keywords=chunk_keywords,
                embedding_hash=content_hash,
                source_file=source_file,
                created_at=now,
            )

            self.documents.append(doc)
            doc_ids.append(doc_id)

            for kw in chunk_keywords:
                if kw not in self.keyword_index:
                    self.keyword_index[kw] = []
                self.keyword_index[kw].append(doc_id)

            if doc_type not in self.doc_type_index:
                self.doc_type_index[doc_type] = []
            self.doc_type_index[doc_type].append(doc_id)

            if self.use_embedding:
                self._compute_embedding(doc)

        return doc_ids

    def _compute_embedding(self, doc: VectorDocument):
        model = self._get_embedding_model()
        if model is None:
            return

        try:
            embedding = model.encode(doc.chunk_text, normalize_embeddings=True)
            self.embedding_cache[doc.doc_id] = embedding.tolist()
        except Exception as e:
            logger.warning("向量索引：计算嵌入向量失败: %s", e)

    def index_law_articles(self, articles: list) -> list[str]:
        all_ids = []
        for article in articles:
            metadata = {
                "law_name": article.law_name,
                "article_number": article.article_number,
                "law_type": article.law_type,
                "hierarchy": article.hierarchy,
                "is_procedural": article.is_procedural,
                "verification_status": article.verification_status,
            }
            ids = self.index_document(
                doc_type="法条",
                title=f"{article.law_name}{article.article_number}",
                content=article.full_text,
                metadata=metadata,
                source_file=article.source_file,
            )
            all_ids.extend(ids)
        return all_ids

    def index_cases(self, cases: list) -> list[str]:
        all_ids = []
        for case in cases:
            content = f"{case.key_holding} {case.applicable_law}"
            metadata = {
                "case_number": case.case_number,
                "court": case.court,
                "case_type": case.case_type,
                "judgment_date": case.judgment_date,
            }
            ids = self.index_document(
                doc_type="案例",
                title=f"{case.case_number} - {case.court}",
                content=content,
                metadata=metadata,
                source_file=case.source_file,
            )
            all_ids.extend(ids)
        return all_ids

    def index_academic_refs(self, refs: list) -> list[str]:
        all_ids = []
        for ref in refs:
            content = f"{ref.key_point} {ref.applicable_law}"
            metadata = {
                "author": ref.author,
                "source": ref.source,
                "year": ref.year,
                "authority_level": ref.authority_level,
            }
            ids = self.index_document(
                doc_type="学术",
                title=ref.title,
                content=content,
                metadata=metadata,
                source_file=ref.source_file,
            )
            all_ids.extend(ids)
        return all_ids

    def index_principles(self, principles: list) -> list[str]:
        all_ids = []
        for principle in principles:
            content = f"{principle.description} {' '.join(principle.examples)}"
            metadata = {
                "source": principle.source,
                "application_scope": principle.application_scope,
            }
            ids = self.index_document(
                doc_type="原则",
                title=principle.name,
                content=content,
                metadata=metadata,
            )
            all_ids.extend(ids)
        return all_ids

    def index_evidence_files(
        self,
        manifest_path: str,
        vault_root: str = "",
    ) -> list[str]:
        all_ids = []

        if not manifest_path or not os.path.exists(manifest_path):
            return all_ids

        try:
            with open(manifest_path, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("向量索引：读取证据清单失败: %s", e)
            return all_ids

        evidence_list = re.findall(r'`([^`]+)`', content)

        for ev_path in evidence_list:
            if vault_root and not os.path.isabs(ev_path):
                full_path = os.path.join(vault_root, ev_path)
            else:
                full_path = ev_path

            if not os.path.exists(full_path):
                continue

            try:
                with open(full_path, encoding="utf-8") as f:
                    ev_content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            ev_name = os.path.basename(full_path)
            ids = self.index_document(
                doc_type="证据",
                title=ev_name,
                content=ev_content,
                metadata={"file_path": full_path},
                source_file=full_path,
            )
            all_ids.extend(ids)

        return all_ids

    def search(
        self,
        query: str,
        doc_types: list[str] | None = None,
        top_k: int = 10,
        min_score: float = 0.1,
    ) -> list[SearchResult]:
        query_keywords = _extract_keywords(query, max_keywords=15)

        if self.use_embedding and self.embedding_cache:
            return self._search_by_embedding(query, doc_types, top_k, min_score)
        else:
            return self._search_by_keywords(query_keywords, doc_types, top_k, min_score)

    def _search_by_keywords(
        self,
        query_keywords: list[str],
        doc_types: list[str] | None = None,
        top_k: int = 10,
        min_score: float = 0.1,
    ) -> list[SearchResult]:
        doc_scores: dict[str, tuple[float, list[str]]] = {}

        for kw in query_keywords:
            matching_ids = self.keyword_index.get(kw, [])
            for doc_id in matching_ids:
                if doc_id not in doc_scores:
                    doc_scores[doc_id] = (0.0, [])
                score, matched = doc_scores[doc_id]
                score += 1.0
                matched.append(kw)
                doc_scores[doc_id] = (score, matched)

        results = []
        for doc_id, (score, matched) in doc_scores.items():
            doc = self._get_doc_by_id(doc_id)
            if doc is None:
                continue

            if doc_types and doc.doc_type not in doc_types:
                continue

            max_possible = len(query_keywords)
            normalized_score = min(score / max(max_possible, 1), 1.0) if max_possible > 0 else 0.0

            if normalized_score >= min_score:
                results.append(SearchResult(
                    doc_id=doc_id,
                    doc_type=doc.doc_type,
                    title=doc.title,
                    chunk_text=doc.chunk_text[:300],
                    score=round(normalized_score, 3),
                    keywords_matched=matched,
                    metadata=doc.metadata,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _search_by_embedding(
        self,
        query: str,
        doc_types: list[str] | None = None,
        top_k: int = 10,
        min_score: float = 0.1,
    ) -> list[SearchResult]:
        model = self._get_embedding_model()
        if model is None:
            query_keywords = _extract_keywords(query)
            return self._search_by_keywords(query_keywords, doc_types, top_k, min_score)

        try:
            query_embedding = model.encode(query, normalize_embeddings=True)
        except Exception as e:
            logger.warning("向量索引：查询嵌入计算失败: %s", e)
            query_keywords = _extract_keywords(query)
            return self._search_by_keywords(query_keywords, doc_types, top_k, min_score)

        results = []
        for doc_id, doc_embedding in self.embedding_cache.items():
            doc = self._get_doc_by_id(doc_id)
            if doc is None:
                continue

            if doc_types and doc.doc_type not in doc_types:
                continue

            similarity = self._cosine_similarity(query_embedding.tolist(), doc_embedding)

            if similarity >= min_score:
                query_keywords = _extract_keywords(query)
                matched = [kw for kw in query_keywords if kw in doc.chunk_text]

                results.append(SearchResult(
                    doc_id=doc_id,
                    doc_type=doc.doc_type,
                    title=doc.title,
                    chunk_text=doc.chunk_text[:300],
                    score=round(similarity, 3),
                    keywords_matched=matched,
                    metadata=doc.metadata,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _get_doc_by_id(self, doc_id: str) -> VectorDocument | None:
        for doc in self.documents:
            if doc.doc_id == doc_id:
                return doc
        return None

    def build_context_for_llm(
        self,
        query: str,
        doc_types: list[str] | None = None,
        top_k: int = 5,
        max_chars: int = 8000,
    ) -> str:
        results = self.search(query, doc_types, top_k)

        if not results:
            return "（未找到相关法律文献）"

        context_parts = []
        total_chars = 0

        for result in results:
            entry = (
                f"### [{result.doc_type}] {result.title}\n"
                f"相关度：{result.score:.1%}\n"
                f"匹配关键词：{', '.join(result.keywords_matched) if result.keywords_matched else '语义匹配'}\n"
                f"内容：{result.chunk_text}\n"
            )

            if total_chars + len(entry) > max_chars:
                break

            context_parts.append(entry)
            total_chars += len(entry)

        header = f"以下为与查询「{query}」最相关的 {len(context_parts)} 条法律文献：\n\n"
        return header + "\n---\n".join(context_parts)

    def get_statistics(self) -> VectorIndexStats:
        doc_types = {}
        for doc in self.documents:
            doc_types[doc.doc_type] = doc_types.get(doc.doc_type, 0) + 1

        index_size = 0
        if self.index_dir and os.path.exists(self.index_dir):
            idx_norm = os.path.normpath(self.index_dir)
            idx_depth = idx_norm.count(os.sep)
            for root, dirs, files in os.walk(self.index_dir):
                current_depth = os.path.normpath(root).count(os.sep) - idx_depth
                if current_depth >= 3:
                    dirs.clear()
                for f in files:
                    index_size += os.path.getsize(os.path.join(root, f))
        index_size_mb = index_size / (1024 * 1024)

        return VectorIndexStats(
            total_documents=len(set(d.doc_id.rsplit('_', 1)[0] for d in self.documents)),
            total_chunks=len(self.documents),
            doc_types=doc_types,
            embedding_mode="向量索引" if self.use_embedding else "关键词索引",
            index_size_mb=round(index_size_mb, 2),
            last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def save_index(self, index_dir: str = "") -> dict:
        save_dir = index_dir or self.index_dir
        if not save_dir:
            return {"success": False, "error": "未指定索引保存目录"}

        os.makedirs(save_dir, exist_ok=True)

        docs_data = [doc.model_dump() for doc in self.documents]
        docs_path = os.path.join(save_dir, "documents.json")
        with open(docs_path, "w", encoding="utf-8") as f:
            json.dump(docs_data, f, ensure_ascii=False, indent=2)

        kw_data = {k: v for k, v in self.keyword_index.items()}
        kw_path = os.path.join(save_dir, "keyword_index.json")
        with open(kw_path, "w", encoding="utf-8") as f:
            json.dump(kw_data, f, ensure_ascii=False, indent=2)

        stats = self.get_statistics()
        stats_path = os.path.join(save_dir, "index_stats.json")
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats.model_dump(), f, ensure_ascii=False, indent=2)

        if self.use_embedding and self.embedding_cache:
            emb_path = os.path.join(save_dir, "embeddings.json")
            with open(emb_path, "w", encoding="utf-8") as f:
                json.dump(self.embedding_cache, f, ensure_ascii=False)

        logger.info("向量索引：已保存到 %s，共 %d 个文档块", save_dir, len(self.documents))
        return {"success": True, "path": save_dir, "total_chunks": len(self.documents)}

    def load_index(self, index_dir: str = "") -> dict:
        load_dir = index_dir or self.index_dir
        if not load_dir or not os.path.exists(load_dir):
            return {"success": False, "error": "索引目录不存在"}

        docs_path = os.path.join(load_dir, "documents.json")
        if os.path.exists(docs_path):
            with open(docs_path, encoding="utf-8") as f:
                docs_data = json.load(f)
            self.documents = [VectorDocument(**d) for d in docs_data]

        kw_path = os.path.join(load_dir, "keyword_index.json")
        if os.path.exists(kw_path):
            with open(kw_path, encoding="utf-8") as f:
                self.keyword_index = json.load(f)

        emb_path = os.path.join(load_dir, "embeddings.json")
        if os.path.exists(emb_path) and self.use_embedding:
            with open(emb_path, encoding="utf-8") as f:
                self.embedding_cache = json.load(f)

        self.loaded = True
        logger.info("向量索引：已从 %s 加载，共 %d 个文档块", load_dir, len(self.documents))
        return {"success": True, "total_chunks": len(self.documents)}

    def clear(self):
        self.documents = []
        self.keyword_index = {}
        self.doc_type_index = {}
        self.embedding_cache = {}
