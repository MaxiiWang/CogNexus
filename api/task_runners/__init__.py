from .base import BaseTaskRunner
from .briefing import BriefingRunner
from .knowledge_digest import KnowledgeDigestRunner
from .stress_test import StressTestRunner
from .expiry_scan import ExpiryScanRunner
from .graph_health import GraphHealthRunner
from .cleanup_scan import CleanupScanRunner

RUNNERS = {
    'briefing': BriefingRunner,
    'knowledge_digest': KnowledgeDigestRunner,
    'stress_test': StressTestRunner,
    'expiry_scan': ExpiryScanRunner,
    'graph_health': GraphHealthRunner,
    'cleanup_scan': CleanupScanRunner,
}

# Task type metadata for frontend
TASK_TYPES = {
    'briefing': {
        'name': '资讯简报',
        'icon': '📰',
        'description': '搜索热点资讯，LLM 撰写简报',
        'default_schedule': '0 8 * * *',
        'default_config': {
            'focus_domains': [],
            'extra_domains': 2,
            'wildcard': True,
        }
    },
    'knowledge_digest': {
        'name': '知识摘要',
        'icon': '🧠',
        'description': '知识库变动、图谱关联、张力检测',
        'default_schedule': '0 21 * * *',
        'default_config': {
            'include_tensions': True,
            'tension_threshold': [0.55, 0.90],
            'include_contradictions': True,
            'challenge_llm': True,
        }
    },
    'stress_test': {
        'name': '观点挑战',
        'icon': '⚔️',
        'description': '识别核心判断，搜索反对意见，生成挑战',
        'default_schedule': '0 20 * * 0',
        'default_config': {
            'challenge_count': 3,
            'search_opposing': True,
            'rebuild_page_index': True,
        }
    },
    'expiry_scan': {
        'name': '过期检测',
        'icon': '📅',
        'description': '扫描即将过期和已过期的知识条目',
        'default_schedule': '0 9 1 * *',
        'default_config': {
            'days_ahead': 30,
        }
    },
    'graph_health': {
        'name': '图谱健康',
        'icon': '🕸️',
        'description': '图谱连通性、健康度指标、枢纽节点',
        'default_schedule': '0 9 * * 1',
        'default_config': {
            'verbose': False,
        }
    },
    'cleanup_scan': {
        'name': '清理建议',
        'icon': '🗑️',
        'description': '检测长期未使用的孤立知识条目',
        'default_schedule': '0 10 1 * *',
        'default_config': {
            'cleanup_days': 60,
        }
    },
}


def get_runner(task_type: str) -> BaseTaskRunner:
    cls = RUNNERS.get(task_type)
    if cls is None:
        raise ValueError(f"Runner for task_type '{task_type}' not implemented")
    return cls()
