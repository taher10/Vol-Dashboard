"use client";

import { useState } from "react";
import { MessageCircle, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type ChatMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Floating chat button + slide-in panel, mounted once in the root layout so
 * it's available on every page. History lives in this component's state
 * only (resets on refresh) -- matches the app's personal-use, no-persistence
 * scope, no need for cross-session storage.
 */
export function ChatPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    const history = messages;
    setMessages([...history, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    try {
      const res = await api.chat(text, history);
      setMessages((m) => [...m, { role: "assistant", content: res.reply }]);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Something went wrong -- try again.";
      setMessages((m) => [...m, { role: "assistant", content: `⚠ ${message}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Button
        variant="default"
        size="icon-lg"
        className="fixed right-6 bottom-6 z-40 rounded-full shadow-lg"
        onClick={() => setOpen(true)}
        aria-label="Ask about the data"
      >
        <MessageCircle className="size-5" />
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="flex w-full flex-col sm:max-w-md">
          <SheetHeader className="border-b border-border">
            <SheetTitle>Ask about the data</SheetTitle>
          </SheetHeader>

          <ScrollArea className="flex-1 px-4">
            <div className="flex flex-col gap-3 py-2">
              {messages.length === 0 && (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  Ask things like &ldquo;which symbol has the highest term structure differential?&rdquo; or
                  &ldquo;does AAPL consistently show put skew above call?&rdquo; -- answers are grounded in the
                  dashboard&apos;s own current and historical data, not a guess. Informational only, not
                  investment advice.
                </p>
              )}
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap",
                    m.role === "user"
                      ? "self-end bg-primary text-primary-foreground"
                      : "self-start bg-muted text-foreground"
                  )}
                >
                  {m.content}
                </div>
              ))}
              {loading && <div className="self-start text-sm text-muted-foreground">Thinking…</div>}
            </div>
          </ScrollArea>

          <SheetFooter className="border-t border-border">
            <div className="flex w-full items-end gap-2">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="Ask a question about the data..."
                className="min-h-10 resize-none"
                rows={1}
              />
              <Button size="icon" onClick={send} disabled={loading || !input.trim()} aria-label="Send">
                <Send className="size-4" />
              </Button>
            </div>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
