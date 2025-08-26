# --- LLM Orchestrator --------------------------------
import os
from typing import Optional, List
import textwrap
from workspace import MemoryWorkspace
from datatypes import Message, Role
import dspy

llm_base = "https://api.deepseek.com"
llm_key = os.getenv("DS_API_KEY")

if llm_key:
    try:
        # Configure DSPy with Deepseek LLM
        dspy.configure(lm=dspy.LM("deepseek/deepseek-chat", api_key=llm_key, base_url=llm_base))
    except Exception:
        # fail explicitly here so we can catch later
        # and degrade gracefully
        raise   

class TimeChunker(dspy.Signature):  
    """Chunk text into smaller pieces based on time references.
    text: str
    -> chunks: List[str]
    """
    role_hint: str = dspy.InputField(default="user")
    text: str = dspy.InputField()
    chunks: List[Message] = dspy.OutputField(
        desc="list of text chunks split by time references, following role hint"
        )
            
            
class Reply(dspy.Signature):
    """Generate an assistant reply.
    instruction: str
    context: str
    -> response: List[Message]
    """
    instruction: str = dspy.InputField()
    question: List[Message] = dspy.InputField(
        desc="user question possibly with time references")
    context:List[Message] = dspy.InputField(
        desc="context block with chat history to keep continuity")
    response: List[Message] = dspy.OutputField(
        desc="list of messages in the reply split by time references if needed")


class Orchestrator:
    """Orchestrates replies and summaries using DSPy if configured, else falls back to local methods."""
      
    def __init__(self, workspace: MemoryWorkspace):
        self.workspace = workspace
        self.dspy_predictor = dspy.Predict(Reply) if llm_key else None
        self.dspy_chunker = dspy.Predict(TimeChunker) if llm_key else None


    def timechunker(self, text: str, role_hint: str = "user") -> List[Message]:
        if self.dspy_chunker:
            try:
                out = self.dspy_chunker(text=text, role_hint=role_hint)
                
                for m in out.chunks:
                        self.workspace.add(m)
                
                return out.chunks
            except Exception as e:
                print(f"TimeChunker error: {e}")
                return [Message(content=f"TimeChunker error: {e}", role=Role.SYSTEM)]
        return [Message(content=text, role=Role.USER)]
    

    def basereply(self, instruction: str, user_text: List[Message]) -> List[Message]:
        if self.dspy_predictor:
            try:
                out = self.dspy_predictor(
                    instruction= instruction or "You are a concise, independent-minded assistant.",
                    question=user_text,
                    context=self.workspace.context_block(n=200))
                
                for m in out.response:
                    self.workspace.add(m)
                
                return out.response
            except Exception as e:
                return self._fallback_reply(user_text='', note=f"(DSPy error: {e})")
        return self._fallback_reply(user_text='', note="(no DSPy configured)")


    def timelinereply(self, user_text: str) -> List[Message]:
   
        # pre-process user input into chunks if possible
        input_chunk = self.timechunker(text=user_text, role_hint="user")

        instruction = textwrap.dedent(
            f"""
            You are a concise, independent-minded assistant. Use the memory context to keep continuity.
            Make good use of the structured time references in the user input if any. 
            When not sure about time reference, implicit assumptions, etc., ask for clarification. 
            """
        ).strip()
        
        return self.basereply(instruction=instruction, 
                              user_text=input_chunk)

    def summarize(self, cue: str = "") -> List[Message]:
        context = self.workspace.context_block(n=50)
        instruction = "Summarize the key facts and decisions from the memory context in bullet points. \
            If applicable construct a timeline of events."
        if self.dspy_predictor:
            try:
                out = self.dspy_predictor(instruction=instruction, 
                                          question=[Message(content=cue, role=Role.USER)] if cue else [],
                                          context=context)
                return out.response
            except Exception as e:
                return self._fallback_summary(note=f"(DSPy error: {e})")
        return self._fallback_summary()

    # --- local fallbacks (no network / no DSPy) -----------------------------
    def _fallback_reply(self, user_text: str, note: str = "") -> List[Message]:
        return [Message(    
            content=f"[local] Echo: {user_text} {note}".strip(), 
            role=Role.ASSISTANT
            )]

    def _fallback_summary(self, note: str = "") -> List[Message]:
        return [Message(
            content=f"[local] Summary not available {note}".strip(), 
            role=Role.ASSISTANT
        )]
