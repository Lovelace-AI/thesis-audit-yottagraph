<template>
    <v-card min-width="500">
        <v-card-title class="d-flex align-center">
            <span>Settings</span>
            <v-spacer></v-spacer>
            <v-btn icon variant="text" @click="state.showSettingsDialog = false">
                <v-icon>mdi-close</v-icon>
            </v-btn>
        </v-card-title>

        <v-divider />

        <v-card-text>
            <v-container>
                <v-row>
                    <v-col cols="12">
                        <h3 class="text-h6 mb-2">Research</h3>
                        <div class="d-flex align-center ga-4 mt-3">
                            <div class="flex-grow-1">
                                <div class="text-body-2">Max research iterations</div>
                                <div class="text-caption text-medium-emphasis">
                                    How many planner loops the researcher agent runs before
                                    stopping. Higher values produce more thorough research but take
                                    longer.
                                </div>
                            </div>
                            <v-text-field
                                v-model.number="maxIterations"
                                type="number"
                                min="1"
                                max="20"
                                density="compact"
                                variant="outlined"
                                hide-details
                                style="max-width: 90px; flex-shrink: 0"
                            />
                        </div>
                    </v-col>
                </v-row>

                <v-divider class="my-4" />

                <v-row>
                    <v-col cols="12">
                        <h3 class="text-h6 mb-2">Server Configuration</h3>
                        <div class="mt-3">
                            <div class="text-body-2 mb-1">Current Query Server:</div>
                            <code class="text-caption">{{
                                currentQueryServer || 'Not configured'
                            }}</code>
                        </div>
                        <div class="text-caption text-medium-emphasis mt-3">
                            Use <code>/configure_query_server</code> in Cursor to change the Query
                            Server address, or update it in the Broadchurch Portal.
                        </div>
                    </v-col>
                </v-row>
            </v-container>
        </v-card-text>

        <v-divider />

        <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn variant="text" @click="state.showSettingsDialog = false">Close</v-btn>
        </v-card-actions>
    </v-card>
</template>

<script setup lang="ts">
    import { state } from '~/utils/appState';
    import { useResearchSettings } from '~/composables/useResearchSettings';

    const config = useRuntimeConfig();
    const currentQueryServer = computed(() => config.public.queryServerAddress as string);

    const { maxIterations } = useResearchSettings();
</script>

<style scoped>
    code {
        padding: 2px 4px;
        background-color: rgba(0, 0, 0, 0.05);
        border-radius: 3px;
        font-family: monospace;
    }
</style>
