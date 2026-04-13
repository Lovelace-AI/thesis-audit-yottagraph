import { ref, computed } from 'vue';
import { readSSE, extractAgentText } from './useAgentChat';
import { useUserState } from './useUserState';
import { searchEntities } from '~/utils/elementalHelpers';

// ---------------------------------------------------------------------------
// Types — QueryRewrite document (the living JSON across rounds)
// ---------------------------------------------------------------------------

export interface EntityCandidate {
    name: string;
    neid: string;
    type?: string;
    score?: number;
}

export interface QueryEntity {
    mentioned_as: string;
    status: 'pending' | 'resolved' | 'unresolved';
    candidates?: EntityCandidate[];
    name?: string;
    neid?: string;
    type?: string;
    user_correction?: string;
}

export interface QueryRewrite {
    thesis_plaintext: string;
    entities: QueryEntity[];
    claims: string[];
    data_needs: string[];
}

// ---------------------------------------------------------------------------
// Types — Report (final output)
// ---------------------------------------------------------------------------

export interface ReportResult {
    query: QueryRewrite;
    calls: any[];
    show_your_work: Record<string, any>;
    supporting_argument: string;
    contradicting_argument: string;
    final_analysis: string;
}

// ---------------------------------------------------------------------------
// Types — Research progress tracking (nested iteration model)
// ---------------------------------------------------------------------------

export interface ResearchCallResult {
    id: number;
    type: string;
    params: Record<string, any>;
    label: string;
    status: 'ok' | 'error';
    summary: string;
}

export interface ResearchIteration {
    id: string;
    iteration: number;
    reasoning: string;
    timestamp: number;
    status: 'planning' | 'executing' | 'done';
    calls: ResearchCallResult[];
    stopReason?: 'complete' | 'max_iterations' | 'planner_error';
}

export interface EntitySelection {
    mentioned_as: string;
    neid: string | null;
    freeText: string | null;
}

export type ResearchStatus =
    | 'idle'
    | 'parsing'
    | 'resolving'
    | 'awaiting_confirmation'
    | 'researching'
    | 'reporting'
    | 'done'
    | 'error';

export interface ErrorDetail {
    message: string;
    stage: string;
    requests: {
        url: string;
        method: string;
        body?: any;
        status?: number;
        contentType?: string;
        responseText?: string;
        error?: string;
    }[];
}

// ---------------------------------------------------------------------------
// Tool name → human-readable label
// ---------------------------------------------------------------------------

const TOOL_LABELS: Record<string, (args: Record<string, any>) => string> = {
    get_news: (a) => `Fetching news for ${a.entity_name || '?'}...`,
    get_stock_prices: (a) => `Getting stock data for ${a.entity_name || '?'}...`,
    get_filings: (a) => `Searching filings for ${a.entity_name || '?'}...`,
    get_events: (a) => `Finding events for ${a.entity_name || '?'}...`,
    get_relationships: (a) => `Exploring relationships for ${a.entity_name || '?'}...`,
    get_entity_properties: (a) => `Getting properties for ${a.entity_name || '?'}...`,
};

function callLabel(type: string, params: Record<string, any>): string {
    const fn = TOOL_LABELS[type];
    return fn ? fn(params) : `Running ${type}...`;
}

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

export function useThesisResearch() {
    const { accessToken } = useUserState();

    const thesis = ref('');
    const status = ref<ResearchStatus>('idle');
    const queryRewrite = ref<QueryRewrite | null>(null);
    const progress = ref<ResearchIteration[]>([]);
    const report = ref<ReportResult | null>(null);
    const rawFallback = ref<string | null>(null);
    const error = ref<string | null>(null);
    const errorDetail = ref<ErrorDetail | null>(null);

    // Per-agent session IDs (separate ADK deployments)
    const sessionIds = ref<{
        queryRewrite: string | null;
        researcher: string | null;
        report: string | null;
    }>({ queryRewrite: null, researcher: null, report: null });

    // Agent engine IDs (resolved once from gateway config)
    const agentIds = ref<{
        queryRewrite: string | null;
        researcher: string | null;
        report: string | null;
    }>({ queryRewrite: null, researcher: null, report: null });

    function getGatewayUrl(): string {
        const cfg = useRuntimeConfig();
        return (cfg.public as any).gatewayUrl || '';
    }

    function getTenantOrgId(): string {
        const cfg = useRuntimeConfig();
        return (cfg.public as any).tenantOrgId || '';
    }

    // -----------------------------------------------------------------------
    // Agent discovery — resolve agent engine IDs from gateway config
    // -----------------------------------------------------------------------

    async function resolveAgents(): Promise<void> {
        if (agentIds.value.queryRewrite) return;

        const gatewayUrl = getGatewayUrl();
        const orgId = getTenantOrgId();
        if (!gatewayUrl || !orgId) return;

        try {
            const cfg = await $fetch<any>(`${gatewayUrl}/api/config/${orgId}`);
            const agents: any[] = cfg?.agents ?? [];

            for (const a of agents) {
                const name = a.name || a.display_name || '';
                if (name === 'query_rewrite') agentIds.value.queryRewrite = a.engine_id;
                else if (name === 'researcher') agentIds.value.researcher = a.engine_id;
                else if (name === 'report') agentIds.value.report = a.engine_id;
            }
        } catch {
            // Gateway not available
        }
    }

    // -----------------------------------------------------------------------
    // ADK function_response unwrapper
    // -----------------------------------------------------------------------

    function unwrapFunctionResponse(raw: any): any | null {
        if (!raw) return null;

        // Case 1: raw is already a parsed object with the fields we expect
        if (typeof raw === 'object' && raw.status) return raw;

        // Case 2: ADK wraps tool returns as {result: "<json string>"}
        if (typeof raw === 'object') {
            const inner = raw.result ?? raw.content ?? raw.output;
            if (typeof inner === 'string') {
                try {
                    return JSON.parse(inner);
                } catch {
                    return null;
                }
            }
            if (typeof inner === 'object' && inner?.status) return inner;
        }

        // Case 3: raw is a plain JSON string
        if (typeof raw === 'string') {
            try {
                return JSON.parse(raw);
            } catch {
                return null;
            }
        }

        return null;
    }

    // -----------------------------------------------------------------------
    // Send a message to a specific agent
    // -----------------------------------------------------------------------

    async function sendToAgent(
        agentKey: 'queryRewrite' | 'researcher' | 'report',
        message: string,
        opts?: { trackProgress?: boolean }
    ): Promise<{ text: string; researchData?: any }> {
        const gatewayUrl = getGatewayUrl();
        const orgId = getTenantOrgId();
        await resolveAgents();

        const engineId = agentIds.value[agentKey];
        if (!gatewayUrl || !orgId) {
            throw new Error('Gateway URL or tenant org ID not configured.');
        }
        if (!engineId) {
            throw new Error(
                `No ${agentKey} agent deployed. Deploy agents first using /deploy_agent.`
            );
        }

        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (accessToken.value) {
            headers['Authorization'] = `Bearer ${accessToken.value}`;
        }
        const body: any = { message };
        if (sessionIds.value[agentKey]) {
            body.session_id = sessionIds.value[agentKey];
        }

        let finalText = '';
        let researchData: any = undefined;
        const debugRequests: ErrorDetail['requests'] = [];

        const localStreamUrl = `/api/agent/${engineId}/stream`;
        const portalStreamUrl = `${gatewayUrl}/api/agents/${orgId}/${engineId}/stream`;
        const portalQueryUrl = `${gatewayUrl}/api/agents/${orgId}/${engineId}/query`;

        const processSSE = async (response: Response): Promise<string | null> => {
            let text = '';
            let lastCallName = '';
            for await (const { event, data } of readSSE(response)) {
                if (event === 'function_call') {
                    lastCallName = data.name || '?';
                    if (opts?.trackProgress && lastCallName === 'research_iteration') {
                        const iteration: ResearchIteration = {
                            id: crypto.randomUUID(),
                            iteration: progress.value.length + 1,
                            reasoning: '',
                            timestamp: Date.now(),
                            status: 'planning',
                            calls: [],
                        };
                        progress.value = [...progress.value, iteration];
                    }
                } else if (event === 'function_response') {
                    const respName = data.name || lastCallName;

                    if (respName === 'research_iteration' && opts?.trackProgress) {
                        const parsed = unwrapFunctionResponse(data.response);

                        if (parsed) {
                            const iterations = [...progress.value];
                            const latest = iterations[iterations.length - 1];
                            if (latest) {
                                latest.reasoning = parsed.reasoning || '';
                                latest.status = 'done';
                                latest.calls = (parsed.calls_made || []).map((c: any) => ({
                                    id: c.id,
                                    type: c.type,
                                    params: c.params || {},
                                    label: callLabel(c.type, c.params || {}),
                                    status: c.status || 'ok',
                                    summary: c.result || '',
                                }));
                                if (parsed.stop_reason) {
                                    latest.stopReason = parsed.stop_reason;
                                }
                                progress.value = iterations;
                            }

                            if (parsed.status === 'done' && parsed.final) {
                                researchData = parsed.final;
                            }
                        }
                    }
                } else if (event === 'text') {
                    text = data.text || text;
                } else if (event === 'done') {
                    if (data.session_id) sessionIds.value[agentKey] = data.session_id;
                    if (data.text) text = data.text;
                    break;
                } else if (event === 'error') {
                    const errMsg =
                        data?.message || data?.error || data?.detail || JSON.stringify(data);
                    debugRequests.push({
                        url: '(SSE error event)',
                        method: 'SSE',
                        responseText: typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg),
                    });
                    return null;
                }
            }
            return text;
        };

        // Try app's own server route (handles auth + Agent Engine proxy)
        try {
            const localResp = await fetch(localStreamUrl, {
                method: 'POST',
                headers,
                body: JSON.stringify(body),
            });
            const localCt = localResp.headers.get('content-type') || '';
            const localDbg: ErrorDetail['requests'][0] = {
                url: localStreamUrl,
                method: 'POST',
                body,
                status: localResp.status,
                contentType: localCt,
            };
            if (localResp.ok && localResp.body && localCt.includes('text/event-stream')) {
                const result = await processSSE(localResp);
                if (result !== null) return { text: result, researchData };
                localDbg.responseText = '(SSE stream returned null — error event or empty)';
            } else if (!localCt.includes('text/event-stream')) {
                localDbg.responseText = await localResp.text().catch(() => '(unreadable)');
            }
            debugRequests.push(localDbg);
        } catch (e: any) {
            debugRequests.push({
                url: localStreamUrl,
                method: 'POST',
                body,
                error: e.message || String(e),
            });
        }

        // Fallback: try portal streaming proxy
        try {
            const portalResp = await fetch(portalStreamUrl, {
                method: 'POST',
                headers,
                body: JSON.stringify(body),
            });
            const portalCt = portalResp.headers.get('content-type') || '';
            const portalDbg: ErrorDetail['requests'][0] = {
                url: portalStreamUrl,
                method: 'POST',
                body,
                status: portalResp.status,
                contentType: portalCt,
            };
            if (portalResp.ok && portalResp.body && portalCt.includes('text/event-stream')) {
                const result = await processSSE(portalResp);
                if (result !== null) return { text: result, researchData };
                portalDbg.responseText = '(SSE stream returned null — error event or empty)';
            } else if (!portalCt.includes('text/event-stream')) {
                portalDbg.responseText = await portalResp.text().catch(() => '(unreadable)');
                if ((portalDbg.responseText?.length ?? 0) > 2000) {
                    portalDbg.responseText =
                        portalDbg.responseText!.slice(0, 2000) + '…[truncated]';
                }
            }
            debugRequests.push(portalDbg);
        } catch (e: any) {
            debugRequests.push({
                url: portalStreamUrl,
                method: 'POST',
                body,
                error: e.message || String(e),
            });
        }

        // Fall back to buffered /query endpoint
        try {
            const queryResp = await $fetch<{ output: any; session_id: string | null }>(
                portalQueryUrl,
                { method: 'POST', headers, body }
            );
            if (queryResp.session_id) sessionIds.value[agentKey] = queryResp.session_id;
            return { text: extractAgentText(queryResp.output) };
        } catch (e: any) {
            debugRequests.push({
                url: portalQueryUrl,
                method: 'POST',
                body,
                error: e.message || String(e),
            });
        }

        // All attempts failed — throw with debug info attached
        const err = new Error(`All agent communication attempts failed for ${agentKey}.`);
        (err as any).debugRequests = debugRequests;
        throw err;
    }

    // -----------------------------------------------------------------------
    // JSON parsing utilities
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

    function extractJSON(text: string): any | null {
        const jsonMatch = text.match(/```json\s*([\s\S]*?)```/);
        const toParse = jsonMatch ? jsonMatch[1].trim() : text.trim();
        const parsed = tryParseJSON(toParse);
        if (parsed) return parsed;

        const braceMatch = text.match(/\{[\s\S]*\}/);
        if (braceMatch) {
            return tryParseJSON(braceMatch[0]);
        }
        return null;
    }

    // -----------------------------------------------------------------------
    // Stage 1: Query Rewrite Loop
    // -----------------------------------------------------------------------

    async function runQueryRewrite(qr: QueryRewrite): Promise<any> {
        const message = JSON.stringify(qr);
        const { text } = await sendToAgent('queryRewrite', message);
        const parsed = extractJSON(text);
        if (!parsed) {
            const err = new Error('Query Rewrite Agent returned unparseable response.');
            (err as any).agentText = text;
            throw err;
        }
        return parsed;
    }

    async function resolveEntities(
        candidates: string[]
    ): Promise<Array<{ mentioned_as: string; candidates: EntityCandidate[] }>> {
        const results = await Promise.all(
            candidates.map(async (name) => {
                try {
                    const matches = await searchEntities(name, { maxResults: 5 });
                    return {
                        mentioned_as: name,
                        candidates: matches.map((m) => ({
                            name: m.name,
                            neid: m.neid,
                            type: m.type,
                            score: m.score,
                        })),
                    };
                } catch {
                    return { mentioned_as: name, candidates: [] };
                }
            })
        );
        return results;
    }

    async function submitThesis(thesisText: string): Promise<void> {
        thesis.value = thesisText;
        status.value = 'parsing';
        queryRewrite.value = null;
        report.value = null;
        rawFallback.value = null;
        progress.value = [];
        error.value = null;
        errorDetail.value = null;
        sessionIds.value = { queryRewrite: null, researcher: null, report: null };

        try {
            const qr: QueryRewrite = {
                thesis_plaintext: thesisText,
                entities: [],
                claims: [],
                data_needs: [],
            };

            const agentResponse = await runQueryRewrite(qr);
            if (!agentResponse) {
                throw new Error('Query Rewrite Agent returned unparseable response.');
            }

            qr.claims = agentResponse.claims || [];
            qr.data_needs = agentResponse.data_needs || [];

            const candidateNames: string[] = agentResponse.candidate_entities || [];
            if (candidateNames.length > 0) {
                status.value = 'resolving';
                const resolved = await resolveEntities(candidateNames);

                qr.entities = resolved.map((r) => ({
                    mentioned_as: r.mentioned_as,
                    status: 'pending' as const,
                    candidates: r.candidates,
                }));
            }

            queryRewrite.value = qr;

            if (qr.entities.length === 0) {
                await runResearchAndReport(qr);
                return;
            }

            status.value = 'awaiting_confirmation';
        } catch (e: any) {
            error.value = e.message || 'Failed to parse thesis.';
            const dbgRequests: ErrorDetail['requests'] = (e as any).debugRequests || [];
            if ((e as any).agentText) {
                dbgRequests.push({
                    url: '(agent response text)',
                    method: 'SSE',
                    responseText: (e as any).agentText,
                });
            }
            errorDetail.value = {
                message: error.value!,
                stage: 'Query Rewrite',
                requests: dbgRequests,
            };
            status.value = 'error';
        }
    }

    // -----------------------------------------------------------------------
    // Stage 1 continued: User confirms or corrects entities
    // -----------------------------------------------------------------------

    async function confirmEntities(selections: EntitySelection[]): Promise<void> {
        if (!queryRewrite.value) return;

        const qr = { ...queryRewrite.value };
        const unresolvedRemain: string[] = [];

        qr.entities = qr.entities.map((entity, idx) => {
            const sel = selections[idx];
            if (!sel) return entity;

            if (sel.neid) {
                const candidate = entity.candidates?.find((c) => c.neid === sel.neid);
                return {
                    mentioned_as: entity.mentioned_as,
                    status: 'resolved' as const,
                    name: candidate?.name || entity.mentioned_as,
                    neid: sel.neid,
                    type: candidate?.type,
                };
            } else if (sel.freeText) {
                unresolvedRemain.push(entity.mentioned_as);
                return {
                    mentioned_as: entity.mentioned_as,
                    status: 'unresolved' as const,
                    user_correction: sel.freeText,
                };
            }
            return entity;
        });

        queryRewrite.value = qr;

        if (unresolvedRemain.length > 0) {
            status.value = 'parsing';
            error.value = null;

            try {
                const agentResponse = await runQueryRewrite(qr);
                if (!agentResponse) {
                    throw new Error('Query Rewrite Agent returned unparseable response.');
                }

                if (agentResponse.claims?.length) qr.claims = agentResponse.claims;
                if (agentResponse.data_needs?.length) qr.data_needs = agentResponse.data_needs;

                const newCandidates: string[] = agentResponse.candidate_entities || [];
                if (newCandidates.length > 0) {
                    status.value = 'resolving';
                    const resolved = await resolveEntities(newCandidates);

                    for (const entity of qr.entities) {
                        if (entity.status !== 'unresolved') continue;
                        const match = resolved.find(
                            (r) =>
                                r.mentioned_as.toLowerCase() ===
                                    entity.user_correction?.toLowerCase() ||
                                r.candidates.some(
                                    (c) =>
                                        c.name.toLowerCase() ===
                                        entity.user_correction?.toLowerCase()
                                )
                        );
                        if (match || resolved.length > 0) {
                            const entityMatch = match || resolved.shift()!;
                            entity.status = 'pending';
                            entity.candidates = entityMatch.candidates;
                            delete entity.user_correction;
                        }
                    }
                }

                queryRewrite.value = { ...qr };
                status.value = 'awaiting_confirmation';
            } catch (e: any) {
                error.value = e.message || 'Failed to re-resolve entities.';
                const dbgRequests: ErrorDetail['requests'] = (e as any).debugRequests || [];
                if ((e as any).agentText) {
                    dbgRequests.push({
                        url: '(agent response text)',
                        method: 'SSE',
                        responseText: (e as any).agentText,
                    });
                }
                errorDetail.value = {
                    message: error.value!,
                    stage: 'Entity Re-resolution',
                    requests: dbgRequests,
                };
                status.value = 'error';
            }
        } else {
            await runResearchAndReport(qr);
        }
    }

    // -----------------------------------------------------------------------
    // Stage 2 + 3: Research then Report
    // -----------------------------------------------------------------------

    async function runResearchAndReport(qr: QueryRewrite): Promise<void> {
        status.value = 'researching';
        progress.value = [];
        error.value = null;
        errorDetail.value = null;

        try {
            const { maxIterations } = useResearchSettings();
            const researchInput = {
                thesis_plaintext: qr.thesis_plaintext,
                entities: qr.entities
                    .filter((e) => e.status === 'resolved')
                    .map((e) => ({
                        name: e.name || e.mentioned_as,
                        neid: e.neid,
                        type: e.type,
                    })),
                claims: qr.claims,
                data_needs: qr.data_needs,
                max_iterations: maxIterations.value,
            };

            const { text: researchText, researchData } = await sendToAgent(
                'researcher',
                JSON.stringify(researchInput),
                { trackProgress: true }
            );

            // Primary path: agent returned structured final data
            let calls = researchData?.research?.calls;
            let showYourWork = researchData?.show_your_work || {};

            // Fallback: reconstruct from accumulated progress iterations
            if (!calls || calls.length === 0) {
                const reconstructed = progress.value.flatMap((iter) =>
                    iter.calls.map((c) => ({
                        id: c.id,
                        type: c.type,
                        params: c.params,
                        status: c.status,
                        result: c.summary,
                    }))
                );
                if (reconstructed.length > 0) {
                    calls = reconstructed;
                    showYourWork = {};
                }
            }

            if (!calls || calls.length === 0) {
                const err = new Error(
                    'Research Agent did not return accumulated data. ' +
                        'The research loop may not have completed.'
                );
                (err as any).agentText = researchText;
                throw err;
            }

            // Stage 3: Report
            status.value = 'reporting';

            const enrichedCalls = calls.map((c: any) => {
                const rawData = showYourWork[c.id];
                if (rawData && Object.keys(rawData).length > 0) {
                    return { ...c, raw_data: rawData };
                }
                return c;
            });

            const reportInput = {
                query: qr,
                calls: enrichedCalls,
            };

            const { text: reportText } = await sendToAgent('report', JSON.stringify(reportInput));

            const reportParsed = extractJSON(reportText);
            if (
                reportParsed &&
                (reportParsed.supporting_argument || reportParsed.contradicting_argument)
            ) {
                report.value = {
                    query: qr,
                    calls,
                    show_your_work: showYourWork,
                    supporting_argument: reportParsed.supporting_argument || '',
                    contradicting_argument: reportParsed.contradicting_argument || '',
                    final_analysis: reportParsed.final_analysis || '',
                };
                status.value = 'done';
            } else {
                rawFallback.value = reportText;
                status.value = 'done';
            }
        } catch (e: any) {
            error.value = e.message || 'Research failed.';
            const dbgRequests: ErrorDetail['requests'] = (e as any).debugRequests || [];
            if ((e as any).agentText) {
                dbgRequests.push({
                    url: '(agent response text)',
                    method: 'SSE',
                    responseText: (e as any).agentText,
                });
            }
            if (progress.value.length > 0) {
                dbgRequests.push({
                    url: '(accumulated progress)',
                    method: 'internal',
                    responseText: JSON.stringify(
                        progress.value.map((it) => ({
                            iteration: it.iteration,
                            status: it.status,
                            reasoning: it.reasoning,
                            calls: it.calls.length,
                        })),
                        null,
                        2
                    ),
                });
            }
            errorDetail.value = {
                message: error.value!,
                stage: 'Research / Report',
                requests: dbgRequests,
            };
            status.value = 'error';
        }
    }

    // -----------------------------------------------------------------------
    // Reset
    // -----------------------------------------------------------------------

    function reset(): void {
        thesis.value = '';
        status.value = 'idle';
        queryRewrite.value = null;
        progress.value = [];
        report.value = null;
        rawFallback.value = null;
        error.value = null;
        errorDetail.value = null;
        sessionIds.value = { queryRewrite: null, researcher: null, report: null };
        agentIds.value = { queryRewrite: null, researcher: null, report: null };
    }

    return {
        thesis: computed(() => thesis.value),
        status: computed(() => status.value),
        queryRewrite: computed(() => queryRewrite.value),
        progress: computed(() => progress.value),
        report: computed(() => report.value),
        rawFallback: computed(() => rawFallback.value),
        error: computed(() => error.value),
        errorDetail: computed(() => errorDetail.value),
        submitThesis,
        confirmEntities,
        reset,
    };
}
