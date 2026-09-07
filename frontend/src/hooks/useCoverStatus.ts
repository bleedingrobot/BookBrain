import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useCoverStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['covers', jobId],
    queryFn: () => api.getCoverStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      query.state.status === 'success' && query.state.data?.status === 'running' ? 2000 : false,
  })
}
