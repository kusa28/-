from pathlib import Path
import tempfile
import unittest
from personal_ai.orchestrator import connect,select_task

class TaskPauseTests(unittest.TestCase):
    def test_paused_task_is_not_selected_even_if_status_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=connect(Path(tmp)/'state.sqlite3')
            try:
                db.execute("UPDATE tasks SET status='COMPLETED'")
                db.execute("UPDATE tasks SET status='PENDING' WHERE id IN ('ASR_QUALITY','RECORDING_KNOWLEDGE')")
                db.execute("UPDATE tasks SET priority=0 WHERE id='ASR_QUALITY'")
                db.execute("INSERT INTO task_pauses VALUES ('ASR_QUALITY','user pause',0)")
                self.assertEqual(select_task(db)['id'],'RECORDING_KNOWLEDGE')
            finally: db.close()
