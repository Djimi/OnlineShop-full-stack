import api from './api';
import type { ItemDTO } from '../types/api';

export const itemsService = {
  getAllItems: async (): Promise<ItemDTO[]> => {
    const response = await api.get<ItemDTO[]>('/items');
    return response.data;
  },

  getItemById: async (id: string): Promise<ItemDTO> => {
    const response = await api.get<ItemDTO>(`/items/${id}`);
    return response.data;
  },
};
