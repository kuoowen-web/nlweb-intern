# state.py
import asyncio

class NLWebHandlerState:

    INITIAL = 0
    DONE = 2

    def __init__(self, handler):
        self.handler = handler
        self.precheck_step_state = {}
        self._state_lock = asyncio.Lock()
        self._decon_event = asyncio.Event()

    def start_precheck_step(self, step_name):
        """Synchronous version for immediate state update"""
        self.precheck_step_state[step_name] = self.__class__.INITIAL

    async def precheck_step_done(self, step_name):
        async with self._state_lock:
            self.precheck_step_state[step_name] = self.__class__.DONE
            if step_name == "Decon":
                self._decon_event.set()
            # Check if all steps are done
            if all(state == self.__class__.DONE for state in self.precheck_step_state.values()):
                self.handler.pre_checks_done_event.set()

    def is_precheck_step_done(self, step_name):
        """CORE-5 (full-scan 批7)：查 step 是否已標 DONE（供 do() 的 finally 判斷是否
        還需補呼叫 precheck_step_done，避免正常路徑重複呼叫）。"""
        return self.precheck_step_state.get(step_name) == self.__class__.DONE

    def set_pre_checks_done(self):
        """Synchronous version for compatibility.

        CORE-5 (full-scan 批7) 死鎖防線（belt）：prepare 尾端 finally 呼叫此方法時，
        除了 pre_checks_done_event，也一併 set _decon_event。這樣即使 Decon do() 在
        precheck_step_done("Decon") 前拋出未攔例外（被 prepare 的 gather
        return_exceptions=True 吞掉），wait_for_decontextualization() 的 waiter 也不會
        永久阻塞——它會醒來後由 is_decontextualization_done() 回報 False（fail-open：
        decontextualized_query 仍是原 query，見各 do() 的 finally fallback）。
        """
        self.handler.pre_checks_done_event.set()
        self._decon_event.set()

    async def pre_check_approval(self):
        """Wait for all pre-checks to complete"""
        await self.handler.pre_checks_done_event.wait()
        if self.handler.query_done:
            return False
        if not self.handler.connection_alive_event.is_set():
            return False
        return True

    async def wait_for_decontextualization(self):
        """Wait for decontextualization to complete"""
        await self._decon_event.wait()
        return self.is_decontextualization_done()

    def is_decontextualization_done(self):
        if "Decon" in self.precheck_step_state:
            return self.precheck_step_state["Decon"] == self.__class__.DONE
        else:
            return False