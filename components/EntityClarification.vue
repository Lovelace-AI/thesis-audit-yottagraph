<template>
    <div class="entity-clarification">
        <div class="clarification-header">
            <h2 class="section-title">Confirm Entities</h2>
            <p class="section-desc">
                We found the following entities in your thesis. Confirm each one is correct, or
                select a different match.
            </p>
        </div>

        <div v-if="clarification.thesis_parsed" class="parsed-thesis">
            <span class="parsed-label">Interpreted as:</span>
            <span class="parsed-text">{{ clarification.thesis_parsed }}</span>
        </div>

        <div class="entity-cards">
            <v-card
                v-for="(entity, idx) in clarification.entities"
                :key="idx"
                class="entity-card"
                variant="outlined"
            >
                <v-card-title class="entity-mentioned">
                    <v-icon size="small" class="mr-2">mdi-format-quote-open</v-icon>
                    {{ entity.mentioned_as }}
                </v-card-title>

                <v-card-text>
                    <template v-if="entity.candidates.length > 0">
                        <v-radio-group
                            v-model="selections[idx].choice"
                            density="compact"
                            hide-details
                        >
                            <v-radio
                                v-for="(candidate, cIdx) in entity.candidates"
                                :key="candidate.neid"
                                :value="cIdx"
                                class="candidate-radio"
                            >
                                <template #label>
                                    <div class="candidate-info">
                                        <div class="candidate-main">
                                            <span class="candidate-name">{{
                                                candidate.resolved_name
                                            }}</span>
                                            <v-chip
                                                size="x-small"
                                                variant="tonal"
                                                :color="typeColor(candidate.entity_type)"
                                            >
                                                {{ candidate.entity_type }}
                                            </v-chip>
                                            <span class="candidate-score">
                                                {{ Math.round(candidate.score * 100) }}%
                                            </span>
                                        </div>
                                        <div v-if="candidate.description" class="candidate-desc">
                                            {{ candidate.description }}
                                        </div>
                                    </div>
                                </template>
                            </v-radio>

                            <v-radio value="other" class="candidate-radio">
                                <template #label>
                                    <span class="other-label">Other...</span>
                                </template>
                            </v-radio>
                        </v-radio-group>

                        <v-text-field
                            v-if="selections[idx].choice === 'other'"
                            v-model="selections[idx].freeText"
                            variant="outlined"
                            density="compact"
                            placeholder="Type the correct entity name..."
                            class="mt-2"
                            hide-details
                        />
                    </template>

                    <template v-else>
                        <v-alert type="warning" variant="tonal" density="compact" class="mb-3">
                            {{ entity.needs_clarification || 'Could not resolve this entity.' }}
                        </v-alert>
                        <v-text-field
                            v-model="selections[idx].freeText"
                            variant="outlined"
                            density="compact"
                            placeholder="Specify the entity you mean..."
                            hide-details
                        />
                    </template>
                </v-card-text>
            </v-card>
        </div>

        <div v-if="clarification.claims.length" class="claims-section">
            <span class="claims-label">Testable claims identified:</span>
            <div class="claims-list">
                <v-chip
                    v-for="claim in clarification.claims"
                    :key="claim"
                    variant="tonal"
                    color="info"
                    size="small"
                >
                    {{ claim }}
                </v-chip>
            </div>
        </div>

        <div class="clarification-actions">
            <v-btn color="primary" size="large" :disabled="!allResolved" @click="handleConfirm">
                Research It
            </v-btn>
            <v-btn variant="text" @click="$emit('edit')"> Edit Thesis </v-btn>
        </div>
    </div>
</template>

<script setup lang="ts">
    import type { ClarificationResponse, EntitySelection } from '~/composables/useThesisResearch';

    const props = defineProps<{
        clarification: ClarificationResponse;
    }>();

    const emit = defineEmits<{
        confirm: [selections: EntitySelection[]];
        edit: [];
    }>();

    interface SelectionState {
        choice: number | 'other';
        freeText: string;
    }

    const selections = ref<SelectionState[]>([]);

    watch(
        () => props.clarification,
        (c) => {
            selections.value = c.entities.map((entity) => ({
                choice: entity.candidates.length > 0 ? (entity.selected_index ?? 0) : 'other',
                freeText: '',
            }));
        },
        { immediate: true }
    );

    const allResolved = computed(() => {
        return selections.value.every((s, idx) => {
            if (s.choice === 'other') return s.freeText.trim().length > 0;
            const entity = props.clarification.entities[idx];
            return entity && entity.candidates.length > 0 && typeof s.choice === 'number';
        });
    });

    function typeColor(entityType: string): string {
        const colors: Record<string, string> = {
            organization: 'primary',
            person: 'info',
            financial_instrument: 'success',
            location: 'warning',
        };
        return colors[entityType] || 'default';
    }

    function handleConfirm() {
        const result: EntitySelection[] = props.clarification.entities.map((entity, idx) => {
            const sel = selections.value[idx];
            if (sel.choice === 'other') {
                return {
                    mentioned_as: entity.mentioned_as,
                    neid: null,
                    freeText: sel.freeText.trim(),
                };
            }
            const candidate = entity.candidates[sel.choice as number];
            return {
                mentioned_as: entity.mentioned_as,
                neid: candidate?.neid ?? null,
                freeText: null,
            };
        });
        emit('confirm', result);
    }
</script>

<style scoped>
    .entity-clarification {
        max-width: 720px;
        width: 100%;
        margin: 0 auto;
    }

    .clarification-header {
        margin-bottom: 20px;
    }

    .section-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1.3rem;
        letter-spacing: 0.02em;
        margin-bottom: 4px;
    }

    .section-desc {
        color: var(--lv-silver);
        font-size: 0.9rem;
    }

    .parsed-thesis {
        background: var(--lv-surface);
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 20px;
    }

    .parsed-label {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
        display: block;
        margin-bottom: 4px;
    }

    .parsed-text {
        font-size: 0.95rem;
        line-height: 1.4;
    }

    .entity-cards {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin-bottom: 20px;
    }

    .entity-card {
        overflow: visible;
    }

    .entity-mentioned {
        font-size: 1rem;
        padding-bottom: 4px;
    }

    .candidate-radio {
        margin-bottom: 4px;
    }

    .candidate-info {
        display: flex;
        flex-direction: column;
        gap: 2px;
    }

    .candidate-main {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .candidate-name {
        font-weight: 500;
    }

    .candidate-score {
        font-family: var(--font-mono);
        font-size: 0.75rem;
        color: var(--lv-silver);
    }

    .candidate-desc {
        font-size: 0.8rem;
        color: var(--lv-silver);
        padding-left: 2px;
    }

    .other-label {
        color: var(--lv-silver);
        font-style: italic;
    }

    .claims-section {
        margin-bottom: 24px;
    }

    .claims-label {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
        display: block;
        margin-bottom: 8px;
    }

    .claims-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

    .clarification-actions {
        display: flex;
        gap: 12px;
        justify-content: center;
    }
</style>
