import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useRebuildStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['rebuild', jobId],
    queryFn: () => api.getRebuildStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1000 : false),
  })
}
