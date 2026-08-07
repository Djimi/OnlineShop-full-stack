package com.onlineshop.gateway.handler;

import com.onlineshop.gateway.dto.ProductInfoResponse;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.function.ServerRequest;
import org.springframework.web.servlet.function.ServerResponse;

@Component
public class ProductInfoHandler {

    private static final ProductInfoResponse RESPONSE =
            new ProductInfoResponse("Random thing");

    public ServerResponse getProductInfo(ServerRequest request) {
        return ServerResponse.ok()
                .contentType(MediaType.APPLICATION_JSON)
                .body(RESPONSE);
    }
}
