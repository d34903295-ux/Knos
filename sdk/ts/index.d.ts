export interface ConnectOptions {
  /** A stable name for this agent: claims are held per agent. */
  agent: string;
  /** The git repo (workspace) whose memory and claims to use. Default: process.cwd(). */
  workspace?: string;
  /** How to start the Knos MCP server. Default: `knos mcp`. */
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  /** Per-call timeout in ms (default 180000: a claim can wait on the chain). */
  timeoutMs?: number;
}

export declare class Knos {
  readonly agent: string;
  /** After a refused claim: who holds it. */
  holder: string | null;
  static connect(options: ConnectOptions): Promise<Knos>;
  claim(unit: string, about?: string): Promise<boolean>;
  release(about?: string): Promise<string>;
  remember(fact: string, about: string): Promise<string>;
  recall(query: string, limit?: number): Promise<string>;
  close(): Promise<void>;
}
