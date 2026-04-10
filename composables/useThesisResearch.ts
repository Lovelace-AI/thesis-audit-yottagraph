import { ref, computed } from 'vue';
import { readSSE, extractAgentText } from './useAgentChat';
import { useUserState } from './useUserState';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EntityCandidate {
    resolved_name: string;
    neid: string;
    entity_type: string;
    description: string;
    score: number;
}

export interface ClarificationEntity {
    mentioned_as: string;
    candidates: EntityCandidate[];
    selected_index: number | null;
    needs_clarification?: string;
}

export interface ClarificationResponse {
    type: 'clarification';
    thesis_parsed: string;
    entities: ClarificationEntity[];
    claims: string[];
}

export interface EvidenceItem {
    source: 'news' | 'filing' | 'stock' | 'event' | 'relationship' | 'macro';
    title: string;
    detail: string;
    date?: string;
    entity?: string;
}

export interface SignalGroup {
    evidence: EvidenceItem[];
    analysis: string;
}

export interface ThesisResults {
    type: 'results';
    thesis_parsed: string;
    entities_examined: string[];
    supporting: SignalGroup;
    contradicting: SignalGroup;
    limitations?: string;
}

export interface ResearchStep {
    id: string;
    tool: string;
    args: Record<string, any>;
    label: string;
    timestamp: number;
}

export interface EntitySelection {
    mentioned_as: string;
    neid: string | null;
    freeText: string | null;
}

export type ResearchStatus =
    | 'idle'
    | 'clarifying'
    | 'awaiting_confirmation'
    | 'researching'
    | 'done'
    | 'error';

// ---------------------------------------------------------------------------
// Tool name → human-readable label
// ---------------------------------------------------------------------------

const TOOL_LABELS: Record<string, (args: Record<string, any>) => string> = {
    lookup_entity: (a) => `Looking up "${a.name || '?'}"...`,
    get_entity_news: (a) => `Fetching news for ${a.entity_name || '?'}...`,
    get_stock_prices: (a) => `Getting stock data for ${a.entity_name || '?'}...`,
    get_entity_filings: (a) => `Searching filings for ${a.entity_name || '?'}...`,
    get_entity_relationships: (a) => `Exploring relationships for ${a.entity_name || '?'}...`,
    get_entity_events: (a) => `Finding events for ${a.entity_name || '?'}...`,
    get_macro_data: (a) => `Searching macro data: "${a.query || '?'}"...`,
    get_schema: () => 'Discovering available data types...',
};

function toolLabel(name: string, args: Record<string, any>): string {
    const fn = TOOL_LABELS[name];
    return fn ? fn(args) : `Running ${name}...`;
}

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

export function useThesisResearch() {
    const { accessToken } = useUserState();

    const thesis = ref('');
    const status = ref<ResearchStatus>('idle');
    const clarification = ref<ClarificationResponse | null>(null);
    const progress = ref<ResearchStep[]>([]);
    const results = ref<ThesisResults | null>(null);
    const rawFallback = ref<string | null>(null);
    const sessionId = ref<string | null>(null);
    const error = ref<string | null>(null);

    function getGatewayUrl(): string {
        const cfg = useRuntimeConfig();
        return (cfg.public as any).gatewayUrl || '';
    }

    function getTenantOrgId(): string {
        const cfg = useRuntimeConfig();
        return (cfg.public as any).tenantOrgId || '';
    }

    // -----------------------------------------------------------------------
    // Agent discovery
    // -----------------------------------------------------------------------

    const agentEngineId = ref<string | null>(null);

    async function resolveAgent(): Promise<string | null> {
        if (agentEngineId.value) return agentEngineId.value;

        const gatewayUrl = getGatewayUrl();
        const orgId = getTenantOrgId();
        if (!gatewayUrl || !orgId) return null;

        try {
            const cfg = await $fetch<any>(`${gatewayUrl}/api/config/${orgId}`);
            const agents: any[] = cfg?.agents ?? [];
            const match = agents.find(
                (a: any) => a.name === 'thesis_researcher' || a.display_name === 'thesis_researcher'
            );
            if (match?.engine_id) {
                agentEngineId.value = match.engine_id;
                return match.engine_id;
            }
            if (agents.length > 0) {
                agentEngineId.value = agents[0].engine_id;
                return agents[0].engine_id;
            }
        } catch {
            // Agent not deployed yet
        }
        return null;
    }

    // -----------------------------------------------------------------------
    // Stream a message to the agent and process events
    // -----------------------------------------------------------------------

    async function sendToAgent(message: string): Promise<string> {
        const gatewayUrl = getGatewayUrl();
        const orgId = getTenantOrgId();
        const engineId = await resolveAgent();

        if (!gatewayUrl || !orgId) {
            throw new Error('Gateway URL or tenant org ID not configured.');
        }
        if (!engineId) {
            throw new Error(
                'No thesis_researcher agent deployed yet. Deploy the agent first using /deploy_agent.'
            );
        }

        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (accessToken.value) {
            headers['Authorization'] = `Bearer ${accessToken.value}`;
        }
        const body: any = { message };
        if (sessionId.value) {
            body.session_id = sessionId.value;
        }

        let finalText = '';

        const localUrl = `/api/agent/${engineId}/stream`;
        const portalStreamUrl = `${gatewayUrl}/api/agents/${orgId}/${engineId}/stream`;
        const portalQueryUrl = `${gatewayUrl}/api/agents/${orgId}/${engineId}/query`;

        const processSSE = async (response: Response): Promise<string | null> => {
            let text = '';
            for await (const { event, data } of readSSE(response)) {
                if (event === 'function_call') {
                    const step: ResearchStep = {
                        id: crypto.randomUUID(),
                        tool: data.name || '?',
                        args: data.args || {},
                        label: toolLabel(data.name || '', data.args || {}),
                        timestamp: Date.now(),
                    };
                    progress.value = [...progress.value, step];
                } else if (event === 'text') {
                    text = data.text || text;
                } else if (event === 'done') {
                    if (data.session_id) sessionId.value = data.session_id;
                    if (data.text) text = data.text;
                    break;
                } else if (event === 'error') {
                    return null;
                }
            }
            return text;
        };

        // Try local server route first (single hop to Agent Engine)
        try {
            const localResp = await fetch(localUrl, {
                method: 'POST',
                headers,
                body: JSON.stringify(body),
            });
            if (localResp.ok && localResp.body) {
                const result = await processSSE(localResp);
                if (result !== null) return result;
            }
        } catch {
            // Local route unavailable
        }

        // Fall back to portal streaming proxy (handles its own auth)
        progress.value = [];
        try {
            const portalResp = await fetch(portalStreamUrl, {
                method: 'POST',
                headers,
                body: JSON.stringify(body),
            });
            if (portalResp.ok && portalResp.body) {
                const result = await processSSE(portalResp);
                if (result !== null) return result;
            }
        } catch {
            // Portal stream unavailable
        }

        // Last resort: buffered /query endpoint
        const queryResp = await $fetch<{ output: any; session_id: string | null }>(portalQueryUrl, {
            method: 'POST',
            headers,
            body,
        });
        if (queryResp.session_id) sessionId.value = queryResp.session_id;
        return extractAgentText(queryResp.output);
    }

    // -----------------------------------------------------------------------
    // Parse structured JSON from agent response
    // -----------------------------------------------------------------------

    function stripTrailingCommas(json: string): string {
        return json.replace(/,\s*([\]}])/g, '$1');
    }

    function tryParseJSON(raw: string): any | null {
        try {
            return JSON.parse(raw);
        } catch {
            try {
                return JSON.parse(stripTrailingCommas(raw));
            } catch {
                return null;
            }
        }
    }

    function parseAgentResponse(text: string): ClarificationResponse | ThesisResults | null {
        const jsonMatch = text.match(/```json\s*([\s\S]*?)```/);
        const toParse = jsonMatch ? jsonMatch[1].trim() : text.trim();

        const parsed = tryParseJSON(toParse);
        if (parsed?.type === 'clarification' || parsed?.type === 'results') {
            return parsed;
        }

        const braceMatch = text.match(/\{[\s\S]*"type"\s*:\s*"(clarification|results)"[\s\S]*\}/);
        if (braceMatch) {
            const fallback = tryParseJSON(braceMatch[0]);
            if (fallback) return fallback;
        }
        return null;
    }

    // -----------------------------------------------------------------------
    // Public methods
    // -----------------------------------------------------------------------

    async function submitThesis(thesisText: string): Promise<void> {
        thesis.value = thesisText;
        status.value = 'clarifying';
        clarification.value = null;
        results.value = null;
        rawFallback.value = null;
        progress.value = [];
        error.value = null;
        sessionId.value = null;

        try {
            const responseText = await sendToAgent(thesisText);
            const parsed = parseAgentResponse(responseText);

            if (parsed?.type === 'clarification') {
                clarification.value = parsed as ClarificationResponse;
                status.value = 'awaiting_confirmation';
            } else if (parsed?.type === 'results') {
                results.value = parsed as ThesisResults;
                status.value = 'done';
            } else {
                rawFallback.value = responseText;
                status.value = 'done';
            }
        } catch (e: any) {
            error.value = e.message || 'Failed to analyze thesis.';
            status.value = 'error';
        }
    }

    async function confirmEntities(selections: EntitySelection[]): Promise<void> {
        const hasFreeText = selections.some((s) => s.freeText);

        let message: string;
        if (hasFreeText) {
            const corrections = selections
                .filter((s) => s.freeText)
                .map((s) => `For "${s.mentioned_as}": ${s.freeText}`)
                .join('\n');
            const confirmed = selections
                .filter((s) => s.neid)
                .map((s) => `For "${s.mentioned_as}": confirmed NEID ${s.neid}`)
                .join('\n');
            message = `Entity corrections:\n${corrections}\n${confirmed}\n\nPlease re-resolve the corrected entities and return an updated clarification.`;
        } else {
            const confirmLines = selections
                .map((s) => `"${s.mentioned_as}": NEID ${s.neid}`)
                .join('\n');
            message = `Entities confirmed:\n${confirmLines}\n\nProceed with full research.`;
        }

        progress.value = [];

        if (hasFreeText) {
            status.value = 'clarifying';
        } else {
            status.value = 'researching';
        }
        error.value = null;

        try {
            const responseText = await sendToAgent(message);
            const parsed = parseAgentResponse(responseText);

            if (parsed?.type === 'clarification') {
                clarification.value = parsed as ClarificationResponse;
                status.value = 'awaiting_confirmation';
            } else if (parsed?.type === 'results') {
                results.value = parsed as ThesisResults;
                status.value = 'done';
            } else {
                rawFallback.value = responseText;
                status.value = 'done';
            }
        } catch (e: any) {
            error.value = e.message || 'Research failed.';
            status.value = 'error';
        }
    }

    function reset(): void {
        thesis.value = '';
        status.value = 'idle';
        clarification.value = null;
        progress.value = [];
        results.value = null;
        rawFallback.value = null;
        sessionId.value = null;
        error.value = null;
        agentEngineId.value = null;
    }

    return {
        thesis: computed(() => thesis.value),
        status: computed(() => status.value),
        clarification: computed(() => clarification.value),
        progress: computed(() => progress.value),
        results: computed(() => results.value),
        rawFallback: computed(() => rawFallback.value),
        error: computed(() => error.value),
        submitThesis,
        confirmEntities,
        reset,
    };
}
