"""Защита шаблонов от удаления — проверки на уровне данных (KnowledgeBase).

Интерфейс лишь показывает состояние; инвариант «защищённый шаблон не удаляется
и не перезаписывается» обязан выполняться здесь, поэтому все сценарии ТЗ
проверяются напрямую через операции базы.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="ares-lock-appdata-"))
sys.path.insert(0, str(ROOT / "app"))

from core import KnowledgeBase  # noqa: E402

ANSWER = "t a {ticket_id} Здравствуйте! Чтобы восстановить доступ, напишите в поддержку сайта и приложите скриншот оплаты. @@@"


class TemplateLockTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.dir.name) / "knowledge.db"
        self.kb = KnowledgeBase(self.db_path, Path(self.dir.name) / "no-seed.ticket")
        # Пустая библиотека: удаляем базовые шаблоны, чтобы считать точно.
        self.kb.delete_all_templates()
        self.ids = [self._create(i) for i in range(6)]

    def tearDown(self):
        self.kb.close()
        self.dir.cleanup()

    def _create(self, index):
        answer = ANSWER.replace("Здравствуйте!", f"Здравствуйте, вариант {index}!")
        return self.kb.create_template(f"Шаблон {index}", answer, True, f"триггер {index} а\nтриггер {index} б", "")

    def _alive(self):
        return {int(row["id"]) for row in self.kb.template_rows()}

    def test_new_and_migrated_templates_are_unlocked(self):
        self.assertTrue(all(int(row["is_locked"]) == 0 for row in self.kb.template_rows()))

    def test_lock_and_unlock(self):
        self.assertEqual(self.kb.set_templates_locked(self.ids[:2], True), 2)
        self.assertEqual(self.kb.set_templates_locked(self.ids[:2], True), 0, "repeat lock changes nothing")
        self.assertEqual(self.kb.locked_template_count(), 2)
        self.assertEqual(self.kb.set_templates_locked([self.ids[0]], False), 1)
        self.assertEqual(self.kb.locked_template_count(), 1)

    def test_single_delete_respects_lock(self):
        self.kb.set_templates_locked([self.ids[0]], True)
        report = self.kb.delete_templates_report([self.ids[0]])
        self.assertEqual(report, {"deleted": 0, "protected": 1, "missing": 0})
        self.assertIn(self.ids[0], self._alive())

    def test_delete_selected_mixed(self):
        self.kb.set_templates_locked(self.ids[:2], True)
        before = self.kb.get_template(self.ids[0])
        report = self.kb.delete_templates_report(self.ids[:4] + [999999])
        self.assertEqual(report, {"deleted": 2, "protected": 2, "missing": 1})
        self.assertEqual(self._alive(), set(self.ids[:2]) | set(self.ids[4:]))
        self.assertEqual(self.kb.get_template(self.ids[0]), before, "protected template kept intact")

    def test_delete_all_keeps_locked_with_content(self):
        self.kb.set_templates_locked(self.ids[:3], True)
        preview = self.kb.deletion_preview(None)
        self.assertEqual(preview["deletable"], 3)
        self.assertEqual(preview["protected"], 3)
        kept = {i: self.kb.get_template(i) for i in self.ids[:3]}
        report = self.kb.delete_all_templates_report()
        self.assertEqual((report["deleted"], report["protected"]), (3, 3))
        self.assertEqual(self._alive(), set(self.ids[:3]))
        for template_id, data in kept.items():
            self.assertEqual(self.kb.get_template(template_id), data)

    def test_all_locked_and_empty_list(self):
        self.kb.set_templates_locked(self.ids, True)
        self.assertEqual(self.kb.deletion_preview(None)["deletable"], 0)
        self.assertEqual(self.kb.delete_all_templates_report()["deleted"], 0)
        self.assertEqual(len(self._alive()), 6)
        self.kb.set_templates_locked(self.ids, False)
        self.kb.delete_all_templates()
        self.assertEqual(self.kb.delete_all_templates_report(), {"deleted": 0, "protected": 0, "missing": 0})
        self.assertEqual(self.kb.delete_templates_report([]), {"deleted": 0, "protected": 0, "missing": 0})

    def test_lock_changed_after_preview_is_respected(self):
        # Диалог показал «удалится 1», но до подтверждения шаблон защитили.
        preview = self.kb.deletion_preview([self.ids[0]])
        self.assertEqual(preview["deletable"], 1)
        self.kb.set_templates_locked([self.ids[0]], True)
        self.assertEqual(self.kb.delete_templates_report([self.ids[0]])["deleted"], 0)
        self.assertIn(self.ids[0], self._alive())

    def test_cleanup_and_editing_keep_locked(self):
        self.kb.set_templates_locked([self.ids[0]], True)
        self.kb.cleanup_suspicious_templates()
        self.assertIn(self.ids[0], self._alive())
        # Защита не мешает редактированию.
        data = self.kb.get_template(self.ids[0])
        self.kb.update_template(self.ids[0], "Новое имя", data["answer_template"], True,
                                data["triggers"], "")
        self.assertEqual(self.kb.get_template(self.ids[0])["name"], "Новое имя")
        self.assertEqual(int(self.kb.get_template(self.ids[0])["is_locked"]), 1)

    def test_persists_after_restart(self):
        self.kb.set_templates_locked([self.ids[1]], True)
        self.kb.close()
        self.kb = KnowledgeBase(self.db_path, Path(self.dir.name) / "no-seed.ticket")
        self.assertEqual(int(self.kb.get_template(self.ids[1])["is_locked"]), 1)

    def test_migration_of_old_database_and_bad_values(self):
        self.kb.close()
        connection = sqlite3.connect(self.db_path)
        # Имитация старой базы без столбца защиты.
        connection.executescript(
            "CREATE TABLE t2 AS SELECT id, answer_key, name, answer_template, enabled, match_mode, triggers, "
            "negative_triggers, close_triggers, priority, reply_limit_enabled, source, created_at, updated_at FROM templates;"
            "DROP TABLE templates; ALTER TABLE t2 RENAME TO templates;"
        )
        connection.commit()
        connection.close()
        self.kb = KnowledgeBase(self.db_path, Path(self.dir.name) / "no-seed.ticket")
        self.assertTrue(all(int(row["is_locked"]) == 0 for row in self.kb.template_rows()))
        connection = sqlite3.connect(self.db_path)
        connection.execute("UPDATE templates SET is_locked='true' WHERE id=?", (self.ids[0],))
        connection.execute("UPDATE templates SET is_locked=7 WHERE id=?", (self.ids[1],))
        connection.commit()
        connection.close()
        self.kb.close()
        self.kb = KnowledgeBase(self.db_path, Path(self.dir.name) / "no-seed.ticket")
        self.assertEqual(int(self.kb.get_template(self.ids[0])["is_locked"]), 1)
        self.assertEqual(int(self.kb.get_template(self.ids[1])["is_locked"]), 0)

    def test_import_keeps_local_protection_and_carries_flag(self):
        self.kb.set_templates_locked([self.ids[0]], True)
        original = self.kb.get_template(self.ids[0])
        bundle = Path(self.dir.name) / "bundle.db"
        export = sqlite3.connect(bundle)
        with self.kb.lock:
            self.kb.connection.backup(export)
        # В пакете шаблоны изменены, а у второго включена защита.
        export.execute("UPDATE templates SET name=name || ' (из пакета)'")
        export.execute("UPDATE templates SET is_locked=1 WHERE id=?", (self.ids[1],))
        export.commit()
        export.close()
        stats = self.kb.merge_from_database(bundle)
        self.assertEqual(stats["templates_protected"], 1)
        self.assertEqual(self.kb.get_template(self.ids[0]), original, "locked local template not overwritten")
        self.assertEqual(int(self.kb.get_template(self.ids[1])["is_locked"]), 1, "lock carried by the bundle")


if __name__ == "__main__":
    unittest.main()
