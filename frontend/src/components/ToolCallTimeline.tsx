import type { ToolCall } from "../api";

interface ToolCallTimelineProps {
  toolCalls: ToolCall[];
}

function ToolCallTimeline({ toolCalls }: ToolCallTimelineProps) {
  if (toolCalls.length === 0) {
    return <p>No tool calls recorded yet.</p>;
  }

  return (
    <ol className="timeline">
      {toolCalls.map((call, index) => (
        <li key={index}>
          <details>
            <summary>
              {index + 1}. {call.tool}
            </summary>
            <h4>Input</h4>
            <pre>{JSON.stringify(call.input, null, 2)}</pre>
            <h4>Output</h4>
            <pre>{JSON.stringify(call.output, null, 2)}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}

export default ToolCallTimeline;
