import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useReidentRebuildStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['reident-rebuild', jobId],
    queryFn: () => api.getReidentRebuildStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      query.state.status === 'success' && query.state.data?.status === 'running' ? 2000 : false,
  })
}
